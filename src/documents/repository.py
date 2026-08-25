"""SQLite metadata, lifecycle transitions, and durable ingestion jobs for private documents."""

from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from src.config import DocumentConfig
from src.events import redact_text
from src.repository import InvalidStateTransitionError, NotFoundError, RepositoryError
from src.state import new_id, utc_now

from .models import (
    DocumentChunk,
    DocumentImage,
    DocumentParent,
    DocumentVersionStatus,
    IngestionJobStatus,
    IngestionStage,
    VisionStatus,
)
from .storage import StoredUpload


class DocumentQuotaExceededError(InvalidStateTransitionError):
    """The owner has exhausted the private document storage quota."""


def _iso_after(seconds: int, *, now: datetime | None = None) -> str:
    base = now or datetime.now(timezone.utc)
    return (base + timedelta(seconds=seconds)).isoformat()


def _iso_now(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


class DocumentRepository:
    """Own document tables in the research SQLite database without touching graph checkpoints."""

    def __init__(self, database_path: str | Path, config: DocumentConfig):
        self.database_path = Path(database_path)
        self.config = config
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    current_version_id TEXT,
                    deleted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (owner_id) REFERENCES users(user_id)
                );
                CREATE TABLE IF NOT EXISTS document_versions (
                    version_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    source_filename TEXT NOT NULL,
                    source_media_type TEXT NOT NULL,
                    source_size INTEGER NOT NULL CHECK(source_size >= 0),
                    source_sha256 TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    markdown_path TEXT,
                    converter_fingerprint TEXT,
                    index_fingerprint TEXT,
                    status TEXT NOT NULL,
                    status_before_delete TEXT,
                    is_current INTEGER NOT NULL DEFAULT 0 CHECK(is_current IN (0, 1)),
                    is_current_before_delete INTEGER,
                    retrieval_enabled INTEGER NOT NULL DEFAULT 0 CHECK(retrieval_enabled IN (0, 1)),
                    retrieval_enabled_before_delete INTEGER,
                    vision_status TEXT NOT NULL DEFAULT 'not_configured',
                    error_code TEXT,
                    error_summary TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(document_id, version_number),
                    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
                    FOREIGN KEY (owner_id) REFERENCES users(user_id)
                );
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    job_id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
                    lease_until TEXT,
                    worker_id TEXT,
                    error_code TEXT,
                    error_summary TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (version_id) REFERENCES document_versions(version_id) ON DELETE CASCADE,
                    FOREIGN KEY (owner_id) REFERENCES users(user_id)
                );
                CREATE TABLE IF NOT EXISTS document_parents (
                    parent_id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL,
                    logical_heading_path TEXT NOT NULL,
                    physical_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    locator TEXT,
                    FOREIGN KEY (version_id) REFERENCES document_versions(version_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS document_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    parent_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    page_start INTEGER,
                    page_end INTEGER,
                    chroma_id TEXT,
                    fts_rowid INTEGER,
                    FOREIGN KEY (parent_id) REFERENCES document_parents(parent_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS document_images (
                    image_id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL,
                    parent_id TEXT,
                    page INTEGER,
                    path TEXT NOT NULL,
                    caption TEXT,
                    vision_status TEXT NOT NULL DEFAULT 'pending',
                    vision_metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (version_id) REFERENCES document_versions(version_id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_id) REFERENCES document_parents(parent_id) ON DELETE SET NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    version_id UNINDEXED,
                    text
                );
                CREATE INDEX IF NOT EXISTS idx_documents_owner_status ON documents(owner_id, deleted_at, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_versions_owner_status ON document_versions(owner_id, status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_versions_retrieval ON document_versions(version_id, retrieval_enabled, is_current);
                CREATE INDEX IF NOT EXISTS idx_versions_document ON document_versions(document_id, version_number DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_claim ON ingestion_jobs(status, lease_until, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_version ON ingestion_jobs(version_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_parents_version ON document_parents(version_id, physical_index);
                CREATE INDEX IF NOT EXISTS idx_chunks_parent ON document_chunks(parent_id);
                CREATE INDEX IF NOT EXISTS idx_images_version ON document_images(version_id);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _bool_fields(record: dict[str, Any]) -> dict[str, Any]:
        for field in ("is_current", "retrieval_enabled", "is_current_before_delete", "retrieval_enabled_before_delete"):
            if record.get(field) is not None:
                record[field] = bool(record[field])
        return record

    @staticmethod
    def _safe_title(filename: str) -> str:
        title = Path(filename).stem.strip() or "未命名文档"
        return title[:200]

    def _require_document(self, connection: sqlite3.Connection, document_id: str, owner_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM documents WHERE document_id = ? AND owner_id = ?", (document_id, owner_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("document not found")
        return dict(row)

    def _require_version(
        self, connection: sqlite3.Connection, document_id: str, version_id: str, owner_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM document_versions WHERE version_id = ? AND document_id = ? AND owner_id = ?",
            (version_id, document_id, owner_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("document version not found")
        return self._bool_fields(dict(row))

    def _quota_used(self, connection: sqlite3.Connection, owner_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(SUM(source_size), 0) AS used_bytes FROM document_versions WHERE owner_id = ?", (owner_id,)
        ).fetchone()
        return int(row["used_bytes"])

    def usage(self, owner_id: str) -> dict[str, int]:
        with self._transaction() as connection:
            used_bytes = self._quota_used(connection, owner_id)
        return {
            "used_bytes": used_bytes,
            "quota_bytes": self.config.user_quota_bytes,
            "remaining_bytes": max(0, self.config.user_quota_bytes - used_bytes),
        }

    def _create_upload(
        self,
        upload: StoredUpload,
        *,
        owner_id: str,
        document_id: str,
        version_id: str,
        existing_document: bool,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            if self._quota_used(connection, owner_id) + upload.source_size > self.config.user_quota_bytes:
                raise DocumentQuotaExceededError("document storage quota exceeded")
            if existing_document:
                document = self._require_document(connection, document_id, owner_id)
                if document["deleted_at"] is not None:
                    raise InvalidStateTransitionError("deleted documents cannot receive a new version")
                next_version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version_number), 0) + 1 AS version FROM document_versions WHERE document_id = ?",
                        (document_id,),
                    ).fetchone()["version"]
                )
                is_current = 0
            else:
                connection.execute(
                    "INSERT INTO documents(document_id, owner_id, title, current_version_id, deleted_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, ?, ?)",
                    (document_id, owner_id, self._safe_title(upload.source_filename), version_id, now, now),
                )
                next_version = 1
                is_current = 1
            connection.execute(
                """
                INSERT INTO document_versions(
                    version_id, document_id, owner_id, version_number, source_filename, source_media_type,
                    source_size, source_sha256, source_path, status, is_current, retrieval_enabled,
                    vision_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    version_id,
                    document_id,
                    owner_id,
                    next_version,
                    upload.source_filename,
                    upload.source_media_type,
                    upload.source_size,
                    upload.source_sha256,
                    upload.source_path,
                    DocumentVersionStatus.QUEUED.value,
                    is_current,
                    VisionStatus.NOT_CONFIGURED.value,
                    now,
                    now,
                ),
            )
            job_id = new_id("ingest")
            connection.execute(
                """
                INSERT INTO ingestion_jobs(job_id, version_id, owner_id, status, stage, attempt, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (job_id, version_id, owner_id, IngestionJobStatus.QUEUED.value, IngestionStage.QUEUED.value, now, now),
            )
            connection.execute("UPDATE documents SET updated_at = ? WHERE document_id = ?", (now, document_id))
        return self.get_document(document_id, owner_id=owner_id)

    def create_document(self, upload: StoredUpload, *, owner_id: str, document_id: str, version_id: str) -> dict[str, Any]:
        return self._create_upload(
            upload,
            owner_id=owner_id,
            document_id=document_id,
            version_id=version_id,
            existing_document=False,
        )

    def create_version(self, document_id: str, upload: StoredUpload, *, owner_id: str, version_id: str) -> dict[str, Any]:
        return self._create_upload(
            upload,
            owner_id=owner_id,
            document_id=document_id,
            version_id=version_id,
            existing_document=True,
        )

    def _versions(self, connection: sqlite3.Connection, document_id: str, owner_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM document_versions WHERE document_id = ? AND owner_id = ? ORDER BY version_number DESC",
            (document_id, owner_id),
        ).fetchall()
        return [self._bool_fields(dict(row)) for row in rows]

    def _jobs(self, connection: sqlite3.Connection, document_id: str, owner_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT jobs.* FROM ingestion_jobs AS jobs
            JOIN document_versions AS versions ON versions.version_id = jobs.version_id
            WHERE versions.document_id = ? AND jobs.owner_id = ?
            ORDER BY jobs.created_at DESC
            """,
            (document_id, owner_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_document(self, document_id: str, *, owner_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            document = self._require_document(connection, document_id, owner_id)
            versions = self._versions(connection, document_id, owner_id)
            jobs = self._jobs(connection, document_id, owner_id)
        current_version = next((version for version in versions if version["version_id"] == document["current_version_id"]), None)
        return {"document": document, "current_version": current_version, "versions": versions, "jobs": jobs}

    def list_documents(
        self, *, owner_id: str, limit: int, offset: int, include_deleted: bool = False
    ) -> tuple[list[dict[str, Any]], int]:
        clause = "" if include_deleted else "AND deleted_at IS NULL"
        with self._transaction() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) AS count FROM documents WHERE owner_id = ? {clause}", (owner_id,)
                ).fetchone()["count"]
            )
            rows = connection.execute(
                f"SELECT * FROM documents WHERE owner_id = ? {clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (owner_id, limit, offset),
            ).fetchall()
            items = []
            for row in rows:
                document = dict(row)
                current = connection.execute(
                    "SELECT * FROM document_versions WHERE version_id = ? AND owner_id = ?",
                    (document["current_version_id"], owner_id),
                ).fetchone()
                items.append({"document": document, "current_version": self._bool_fields(dict(current)) if current else None})
        return items, total

    def _require_active_job(self, connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT jobs.*, versions.document_id, versions.status AS version_status, documents.deleted_at
            FROM ingestion_jobs AS jobs
            JOIN document_versions AS versions ON versions.version_id = jobs.version_id
            JOIN documents AS documents ON documents.document_id = versions.document_id
            WHERE jobs.job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("ingestion job not found")
        job = dict(row)
        if (
            job["status"] != IngestionJobStatus.PROCESSING.value
            or job["version_status"] != DocumentVersionStatus.PROCESSING.value
            or job["deleted_at"] is not None
        ):
            raise InvalidStateTransitionError("ingestion job is no longer active")
        return job

    def update_ingestion_stage(self, job_id: str, stage: IngestionStage) -> None:
        """Advance the visible stage only while the claimed job and document remain active."""

        now = utc_now()
        with self._transaction(immediate=True) as connection:
            self._require_active_job(connection, job_id)
            connection.execute(
                "UPDATE ingestion_jobs SET stage = ?, updated_at = ? WHERE job_id = ?",
                (stage.value, now, job_id),
            )

    def record_conversion(
        self,
        job_id: str,
        *,
        markdown_path: str,
        converter_fingerprint: str,
    ) -> None:
        """Persist conversion provenance without storing source text in SQLite."""

        now = utc_now()
        with self._transaction(immediate=True) as connection:
            job = self._require_active_job(connection, job_id)
            connection.execute(
                """
                UPDATE document_versions
                SET markdown_path = ?, converter_fingerprint = ?, updated_at = ?
                WHERE version_id = ?
                """,
                (markdown_path, converter_fingerprint[:256], now, job["version_id"]),
            )

    def replace_ingestion_artifacts(
        self,
        job_id: str,
        *,
        parents: Sequence[DocumentParent],
        chunks: Sequence[DocumentChunk],
        images: Sequence[DocumentImage],
        vision_status: VisionStatus,
    ) -> None:
        """Atomically replace retryable conversion/chunking artifacts for one leased version."""

        now = utc_now()
        with self._transaction(immediate=True) as connection:
            job = self._require_active_job(connection, job_id)
            version_id = str(job["version_id"])
            parent_ids = {parent.parent_id for parent in parents}
            if not parents or any(parent.version_id != version_id for parent in parents):
                raise RepositoryError("ingestion parents must belong to the claimed document version")
            if any(chunk.parent_id not in parent_ids for chunk in chunks):
                raise RepositoryError("ingestion chunks must reference a persisted parent")
            if any(image.version_id != version_id or (image.parent_id and image.parent_id not in parent_ids) for image in images):
                raise RepositoryError("ingestion images must belong to the claimed document version")

            connection.execute("DELETE FROM document_chunks_fts WHERE version_id = ?", (version_id,))
            connection.execute("DELETE FROM document_images WHERE version_id = ?", (version_id,))
            connection.execute("DELETE FROM document_parents WHERE version_id = ?", (version_id,))
            connection.executemany(
                """
                INSERT INTO document_parents(parent_id, version_id, logical_heading_path, physical_index, text, locator)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        parent.parent_id,
                        parent.version_id,
                        parent.logical_heading_path,
                        parent.physical_index,
                        parent.text,
                        parent.locator,
                    )
                    for parent in parents
                ],
            )
            connection.executemany(
                """
                INSERT INTO document_chunks(chunk_id, parent_id, kind, text, page_start, page_end, chroma_id, fts_rowid)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.parent_id,
                        chunk.kind,
                        chunk.text,
                        chunk.page_start,
                        chunk.page_end,
                    )
                    for chunk in chunks
                ],
            )
            connection.executemany(
                """
                INSERT INTO document_images(image_id, version_id, parent_id, page, path, caption, vision_status, vision_metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        image.image_id,
                        image.version_id,
                        image.parent_id,
                        image.page,
                        image.path,
                        image.caption,
                        image.vision_status.value,
                        json.dumps(image.vision_metadata, ensure_ascii=True, sort_keys=True),
                    )
                    for image in images
                ],
            )
            connection.execute(
                "UPDATE document_versions SET vision_status = ?, updated_at = ? WHERE version_id = ?",
                (vision_status.value, now, version_id),
            )

    def mark_version_ready(self, document_id: str, version_id: str, *, owner_id: str) -> dict[str, Any]:
        """Atomically promote a successfully indexed version and archive the prior current version."""

        now = utc_now()
        with self._transaction(immediate=True) as connection:
            document = self._require_document(connection, document_id, owner_id)
            version = self._require_version(connection, document_id, version_id, owner_id)
            if document["deleted_at"] is not None or version["status"] == DocumentVersionStatus.DELETED.value:
                raise InvalidStateTransitionError("deleted documents cannot become ready")
            if document["current_version_id"] != version_id:
                connection.execute(
                    """
                    UPDATE document_versions
                    SET status = ?, is_current = 0, retrieval_enabled = 0, updated_at = ?
                    WHERE document_id = ? AND owner_id = ? AND is_current = 1 AND version_id != ?
                    """,
                    (DocumentVersionStatus.ARCHIVED.value, now, document_id, owner_id, version_id),
                )
                connection.execute(
                    "UPDATE documents SET current_version_id = ?, updated_at = ? WHERE document_id = ?",
                    (version_id, now, document_id),
                )
            connection.execute(
                """
                UPDATE document_versions
                SET status = ?, is_current = 1, retrieval_enabled = 1, error_code = NULL, error_summary = NULL, updated_at = ?
                WHERE version_id = ? AND document_id = ? AND owner_id = ?
                """,
                (DocumentVersionStatus.READY.value, now, version_id, document_id, owner_id),
            )
            connection.execute(
                """
                UPDATE ingestion_jobs SET status = ?, stage = ?, lease_until = NULL, worker_id = NULL, updated_at = ?
                WHERE version_id = ? AND status = ?
                """,
                (IngestionJobStatus.SUCCEEDED.value, IngestionStage.COMPLETE.value, now, version_id, IngestionJobStatus.PROCESSING.value),
            )
        return self.get_document(document_id, owner_id=owner_id)

    def delete_document(self, document_id: str, *, owner_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            document = self._require_document(connection, document_id, owner_id)
            if document["deleted_at"] is not None:
                return self.get_document(document_id, owner_id=owner_id)
            connection.execute("UPDATE documents SET deleted_at = ?, updated_at = ? WHERE document_id = ?", (now, now, document_id))
            connection.execute(
                """
                UPDATE document_versions
                SET status_before_delete = status, is_current_before_delete = is_current,
                    retrieval_enabled_before_delete = retrieval_enabled, status = ?, is_current = 0,
                    retrieval_enabled = 0, updated_at = ?
                WHERE document_id = ? AND owner_id = ?
                """,
                (DocumentVersionStatus.DELETED.value, now, document_id, owner_id),
            )
            connection.execute(
                """
                UPDATE ingestion_jobs SET status = ?, lease_until = NULL, worker_id = NULL, updated_at = ?
                WHERE owner_id = ? AND version_id IN (SELECT version_id FROM document_versions WHERE document_id = ?)
                  AND status IN (?, ?)
                """,
                (
                    IngestionJobStatus.CANCELLED.value,
                    now,
                    owner_id,
                    document_id,
                    IngestionJobStatus.QUEUED.value,
                    IngestionJobStatus.PROCESSING.value,
                ),
            )
        return self.get_document(document_id, owner_id=owner_id)

    def restore_document(self, document_id: str, *, owner_id: str, now: datetime | None = None) -> dict[str, Any]:
        restored_at = _iso_now(now)
        with self._transaction(immediate=True) as connection:
            document = self._require_document(connection, document_id, owner_id)
            deleted_at = document["deleted_at"]
            if deleted_at is None:
                return self.get_document(document_id, owner_id=owner_id)
            deleted_time = datetime.fromisoformat(deleted_at)
            cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.config.purge_retention_days)
            if deleted_time < cutoff:
                raise InvalidStateTransitionError("document recovery window has expired")
            connection.execute("UPDATE documents SET deleted_at = NULL, updated_at = ? WHERE document_id = ?", (restored_at, document_id))
            connection.execute(
                """
                UPDATE document_versions
                SET status = CASE
                        WHEN status_before_delete IN (?, ?) THEN ?
                        ELSE COALESCE(status_before_delete, ?)
                    END,
                    is_current = COALESCE(is_current_before_delete, 0),
                    retrieval_enabled = COALESCE(retrieval_enabled_before_delete, 0),
                    status_before_delete = NULL, is_current_before_delete = NULL,
                    retrieval_enabled_before_delete = NULL, updated_at = ?
                WHERE document_id = ? AND owner_id = ?
                """,
                (
                    DocumentVersionStatus.QUEUED.value,
                    DocumentVersionStatus.PROCESSING.value,
                    DocumentVersionStatus.QUEUED.value,
                    DocumentVersionStatus.FAILED.value,
                    restored_at,
                    document_id,
                    owner_id,
                ),
            )
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = ?, stage = ?, lease_until = NULL, worker_id = NULL,
                    error_code = NULL, error_summary = NULL, updated_at = ?
                WHERE status = ? AND version_id IN (
                    SELECT version_id FROM document_versions
                    WHERE document_id = ? AND owner_id = ? AND status = ?
                )
                """,
                (
                    IngestionJobStatus.QUEUED.value,
                    IngestionStage.QUEUED.value,
                    restored_at,
                    IngestionJobStatus.CANCELLED.value,
                    document_id,
                    owner_id,
                    DocumentVersionStatus.QUEUED.value,
                ),
            )
        return self.get_document(document_id, owner_id=owner_id)

    def retry_version(self, document_id: str, version_id: str, *, owner_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            document = self._require_document(connection, document_id, owner_id)
            version = self._require_version(connection, document_id, version_id, owner_id)
            if document["deleted_at"] is not None:
                raise InvalidStateTransitionError("deleted documents cannot be retried")
            if version["status"] != DocumentVersionStatus.FAILED.value:
                raise InvalidStateTransitionError("only failed versions can be retried")
            latest_job = connection.execute(
                "SELECT job_id, attempt FROM ingestion_jobs WHERE version_id = ? AND owner_id = ? ORDER BY created_at DESC LIMIT 1",
                (version_id, owner_id),
            ).fetchone()
            if latest_job is None:
                raise RepositoryError("document version has no ingestion job")
            connection.execute(
                """
                UPDATE ingestion_jobs SET status = ?, stage = ?, attempt = 0, lease_until = NULL, worker_id = NULL,
                    error_code = NULL, error_summary = NULL, updated_at = ? WHERE job_id = ?
                """,
                (IngestionJobStatus.QUEUED.value, IngestionStage.QUEUED.value, now, latest_job["job_id"]),
            )
            connection.execute(
                """
                UPDATE document_versions SET status = ?, error_code = NULL, error_summary = NULL, updated_at = ?
                WHERE version_id = ?
                """,
                (DocumentVersionStatus.QUEUED.value, now, version_id),
            )
        return self.get_document(document_id, owner_id=owner_id)

    def _recover_expired_jobs(self, connection: sqlite3.Connection, *, now: datetime) -> None:
        rows = connection.execute(
            "SELECT job_id, version_id, attempt FROM ingestion_jobs WHERE status = ? AND lease_until IS NOT NULL AND lease_until <= ?",
            (IngestionJobStatus.PROCESSING.value, now.isoformat()),
        ).fetchall()
        for row in rows:
            status = IngestionJobStatus.FAILED.value if int(row["attempt"]) >= self.config.job_max_attempts else IngestionJobStatus.QUEUED.value
            code = "LEASE_EXPIRED" if status == IngestionJobStatus.FAILED.value else None
            summary = "document ingestion lease expired" if status == IngestionJobStatus.FAILED.value else None
            connection.execute(
                """
                UPDATE ingestion_jobs SET status = ?, stage = ?, lease_until = NULL, worker_id = NULL,
                    error_code = ?, error_summary = ?, updated_at = ? WHERE job_id = ?
                """,
                (status, IngestionStage.QUEUED.value, code, summary, now.isoformat(), row["job_id"]),
            )
            connection.execute(
                "UPDATE document_versions SET status = ?, error_code = ?, error_summary = ?, updated_at = ? WHERE version_id = ?",
                (
                    DocumentVersionStatus.FAILED.value if status == IngestionJobStatus.FAILED.value else DocumentVersionStatus.QUEUED.value,
                    code,
                    summary,
                    now.isoformat(),
                    row["version_id"],
                ),
            )

    def claim_next_job(self, worker_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        """Atomically recover expired leases and claim one eligible job for a worker."""

        current_time = now or datetime.now(timezone.utc)
        with self._transaction(immediate=True) as connection:
            self._recover_expired_jobs(connection, now=current_time)
            row = connection.execute(
                """
                SELECT jobs.*, versions.document_id, versions.source_path, versions.source_filename,
                       versions.source_media_type FROM ingestion_jobs AS jobs
                JOIN document_versions AS versions ON versions.version_id = jobs.version_id
                JOIN documents AS documents ON documents.document_id = versions.document_id
                WHERE jobs.status = ? AND versions.status = ? AND documents.deleted_at IS NULL
                ORDER BY jobs.created_at ASC LIMIT 1
                """,
                (IngestionJobStatus.QUEUED.value, DocumentVersionStatus.QUEUED.value),
            ).fetchone()
            if row is None:
                return None
            job = dict(row)
            lease_until = _iso_after(self.config.job_lease_seconds, now=current_time)
            updated_at = current_time.isoformat()
            connection.execute(
                """
                UPDATE ingestion_jobs SET status = ?, stage = ?, attempt = attempt + 1, lease_until = ?, worker_id = ?, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    IngestionJobStatus.PROCESSING.value,
                    IngestionStage.CONVERTING.value,
                    lease_until,
                    worker_id,
                    updated_at,
                    job["job_id"],
                    IngestionJobStatus.QUEUED.value,
                ),
            )
            connection.execute(
                "UPDATE document_versions SET status = ?, updated_at = ? WHERE version_id = ?",
                (DocumentVersionStatus.PROCESSING.value, updated_at, job["version_id"]),
            )
            job.update(
                {
                    "status": IngestionJobStatus.PROCESSING.value,
                    "stage": IngestionStage.CONVERTING.value,
                    "attempt": int(job["attempt"]) + 1,
                    "lease_until": lease_until,
                    "worker_id": worker_id,
                    "updated_at": updated_at,
                }
            )
            return job

    def fail_job(self, job_id: str, *, error_code: str, error_summary: str) -> dict[str, Any]:
        """Record a bounded, redacted failure without retaining private document content."""

        now = utc_now()
        safe_summary = redact_text(error_summary, max_length=500)
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise NotFoundError("ingestion job not found")
            job = dict(row)
            if job["status"] != IngestionJobStatus.PROCESSING.value:
                raise InvalidStateTransitionError("only claimed jobs can fail")
            connection.execute(
                """
                UPDATE ingestion_jobs SET status = ?, lease_until = NULL, worker_id = NULL, error_code = ?, error_summary = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (IngestionJobStatus.FAILED.value, error_code[:100], safe_summary, now, job_id),
            )
            connection.execute(
                """
                UPDATE document_versions SET status = ?, error_code = ?, error_summary = ?, updated_at = ? WHERE version_id = ?
                """,
                (DocumentVersionStatus.FAILED.value, error_code[:100], safe_summary, now, job["version_id"]),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
        with self._transaction() as connection:
            if owner_id is None:
                row = connection.execute("SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM ingestion_jobs WHERE job_id = ? AND owner_id = ?", (job_id, owner_id)
                ).fetchone()
        if row is None:
            raise NotFoundError("ingestion job not found")
        return dict(row)
