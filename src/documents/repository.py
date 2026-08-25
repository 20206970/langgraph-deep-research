"""SQLite metadata, lifecycle transitions, and durable ingestion jobs for private documents."""

from __future__ import annotations

import sqlite3
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from src.config import DocumentConfig
from src.events import redact_text
from src.repository import InvalidStateTransitionError, NotFoundError, RepositoryError
from src.state import DocumentScope, new_id, utc_now

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


_FTS_TERM = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+")


def _fts_index_text(text: str) -> str:
    """Retain normal FTS tokens and add CJK unigrams for SQLite's built-in tokenizer."""

    cjk_terms = [term for term in _FTS_TERM.findall(text) if len(term) == 1 and "\u3400" <= term <= "\u9fff"]
    return text if not cjk_terms else f"{text}\n{' '.join(cjk_terms)}"


def _fts_query(query: str) -> str:
    terms = _FTS_TERM.findall(query)
    if not terms:
        return ""
    # Terms are generated locally and quoted, so user input cannot alter FTS5 grammar.
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)


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

    def resolve_document_scope(
        self,
        *,
        owner_id: str,
        document_ids: Sequence[str] = (),
        use_all_my_documents: bool = False,
    ) -> DocumentScope:
        """Freeze the owner's currently retrievable versions for one research run.

        Callers provide document IDs, never version IDs. This prevents a client from
        selecting an archived version or bypassing the document lifecycle checks.
        """

        normalized_ids = [str(document_id).strip() for document_id in document_ids]
        if any(not document_id for document_id in normalized_ids):
            raise InvalidStateTransitionError("document IDs cannot be blank")
        if len(normalized_ids) != len(set(normalized_ids)):
            raise InvalidStateTransitionError("document IDs must be unique")
        if normalized_ids and use_all_my_documents:
            raise InvalidStateTransitionError("document IDs and all-documents selection cannot be combined")
        if not normalized_ids and not use_all_my_documents:
            return DocumentScope()

        selection_mode = "all_my_documents" if use_all_my_documents else "explicit"
        with self._transaction() as connection:
            if normalized_ids:
                placeholders = ", ".join("?" for _ in normalized_ids)
                owned_rows = connection.execute(
                    "SELECT document_id FROM documents WHERE owner_id = ? AND document_id IN (" + placeholders + ")",
                    [owner_id, *normalized_ids],
                ).fetchall()
                owned_ids = {str(row["document_id"]) for row in owned_rows}
                if owned_ids != set(normalized_ids):
                    # Match the rest of the authenticated API: a foreign document is not
                    # distinguishable from an unknown document.
                    raise NotFoundError("document not found")
                selection_clause = "AND documents.document_id IN (" + placeholders + ")"
                parameters: list[Any] = [owner_id, *normalized_ids]
            else:
                selection_clause = ""
                parameters = [owner_id]

            rows = connection.execute(
                """
                SELECT documents.document_id, versions.version_id
                FROM documents
                JOIN document_versions AS versions ON versions.version_id = documents.current_version_id
                WHERE documents.owner_id = ?
                  AND documents.deleted_at IS NULL
                  AND versions.owner_id = documents.owner_id
                  AND versions.status = ?
                  AND versions.is_current = 1
                  AND versions.retrieval_enabled = 1
                """
                + selection_clause
                + " ORDER BY documents.document_id ASC",
                [*parameters[:1], DocumentVersionStatus.READY.value, *parameters[1:]],
            ).fetchall()

        if normalized_ids:
            ready_ids = {str(row["document_id"]) for row in rows}
            if ready_ids != set(normalized_ids):
                raise InvalidStateTransitionError("selected document is not ready for retrieval")
        return DocumentScope(
            selection_mode=selection_mode,
            version_ids=[str(row["version_id"]) for row in rows],
        )

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

    def indexable_chunks_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Return the claimed version's chunks with the metadata required by the isolated vector collection."""

        with self._transaction() as connection:
            job = self._require_active_job(connection, job_id)
            rows = connection.execute(
                """
                SELECT chunks.chunk_id, chunks.text, chunks.kind, chunks.page_start, chunks.page_end,
                       parents.parent_id, parents.logical_heading_path, parents.physical_index, parents.locator,
                       versions.version_id, versions.document_id, versions.owner_id, documents.title
                FROM document_chunks AS chunks
                JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
                JOIN document_versions AS versions ON versions.version_id = parents.version_id
                JOIN documents AS documents ON documents.document_id = versions.document_id
                WHERE versions.version_id = ?
                ORDER BY parents.physical_index ASC, chunks.rowid ASC
                """,
                (job["version_id"],),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_chunk_index_entries(
        self,
        job_id: str,
        *,
        chroma_ids: dict[str, str],
        index_fingerprint: str,
    ) -> None:
        """Persist the FTS side and vector IDs as one transaction after a successful vector upsert."""

        now = utc_now()
        with self._transaction(immediate=True) as connection:
            job = self._require_active_job(connection, job_id)
            version_id = str(job["version_id"])
            rows = connection.execute(
                """
                SELECT chunks.chunk_id, chunks.text FROM document_chunks AS chunks
                JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
                WHERE parents.version_id = ? ORDER BY parents.physical_index ASC, chunks.rowid ASC
                """,
                (version_id,),
            ).fetchall()
            expected_ids = {str(row["chunk_id"]) for row in rows}
            if not expected_ids or expected_ids != set(chroma_ids):
                raise RepositoryError("index entries must cover exactly the claimed document chunks")
            connection.execute("DELETE FROM document_chunks_fts WHERE version_id = ?", (version_id,))
            for row in rows:
                cursor = connection.execute(
                    "INSERT INTO document_chunks_fts(chunk_id, version_id, text) VALUES (?, ?, ?)",
                    (row["chunk_id"], version_id, _fts_index_text(str(row["text"]))),
                )
                connection.execute(
                    "UPDATE document_chunks SET chroma_id = ?, fts_rowid = ? WHERE chunk_id = ?",
                    (chroma_ids[str(row["chunk_id"])], cursor.lastrowid, row["chunk_id"]),
                )
            connection.execute(
                "UPDATE document_versions SET index_fingerprint = ?, updated_at = ? WHERE version_id = ?",
                (index_fingerprint[:256], now, version_id),
            )

    def document_vector_states(self, document_id: str, *, owner_id: str) -> list[dict[str, Any]]:
        """Return authoritative vector metadata after a lifecycle transition for one owned document."""

        with self._transaction() as connection:
            self._require_document(connection, document_id, owner_id)
            rows = connection.execute(
                """
                SELECT chunks.chroma_id, chunks.chunk_id, chunks.kind, chunks.page_start, chunks.page_end,
                       parents.parent_id, versions.version_id, versions.document_id, versions.owner_id,
                       versions.retrieval_enabled, versions.status, documents.deleted_at
                FROM document_chunks AS chunks
                JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
                JOIN document_versions AS versions ON versions.version_id = parents.version_id
                JOIN documents AS documents ON documents.document_id = versions.document_id
                WHERE versions.document_id = ? AND versions.owner_id = ? AND chunks.chroma_id IS NOT NULL
                """,
                (document_id, owner_id),
            ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record["is_deleted"] = record["deleted_at"] is not None or record["status"] == DocumentVersionStatus.DELETED.value
            record["retrieval_enabled"] = bool(record["retrieval_enabled"]) and not record["is_deleted"]
            records.append(record)
        return records

    @staticmethod
    def _version_placeholders(version_ids: Sequence[str]) -> str:
        if not version_ids:
            raise ValueError("at least one allowed document version is required")
        return ", ".join("?" for _ in version_ids)

    def _eligible_chunk_rows(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        version_ids: Sequence[str],
        chunk_ids: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        version_placeholders = self._version_placeholders(version_ids)
        clauses = [
            "versions.owner_id = ?",
            "versions.version_id IN (" + version_placeholders + ")",
            "versions.status = ?",
            "versions.is_current = 1",
            "versions.retrieval_enabled = 1",
            "documents.deleted_at IS NULL",
        ]
        parameters: list[Any] = [owner_id, *version_ids, DocumentVersionStatus.READY.value]
        if chunk_ids is not None:
            if not chunk_ids:
                return {}
            clauses.append("chunks.chunk_id IN (" + ", ".join("?" for _ in chunk_ids) + ")")
            parameters.extend(chunk_ids)
        rows = connection.execute(
            """
            SELECT chunks.chunk_id, chunks.parent_id, chunks.kind, chunks.text, chunks.page_start, chunks.page_end,
                   chunks.chroma_id, chunks.fts_rowid, parents.logical_heading_path, parents.physical_index,
                   parents.locator, parents.text AS parent_text, versions.version_id, versions.document_id,
                   documents.title
            FROM document_chunks AS chunks
            JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
            JOIN document_versions AS versions ON versions.version_id = parents.version_id
            JOIN documents AS documents ON documents.document_id = versions.document_id
            WHERE """
            + " AND ".join(clauses),
            parameters,
        ).fetchall()
        return {str(row["chunk_id"]): dict(row) for row in rows}

    def eligible_chunks(
        self, chunk_ids: Sequence[str], *, owner_id: str, version_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Re-authorize vector candidates against SQLite before returning their private text."""

        with self._transaction() as connection:
            records = self._eligible_chunk_rows(
                connection, owner_id=owner_id, version_ids=version_ids, chunk_ids=chunk_ids
            )
        return [records[chunk_id] for chunk_id in chunk_ids if chunk_id in records]

    def bm25_chunks(
        self,
        query: str,
        *,
        owner_id: str,
        version_ids: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return eligible FTS5 candidates; text is always rejoined to current lifecycle state."""

        fts_query = _fts_query(query)
        if not fts_query or not version_ids:
            return []
        version_placeholders = self._version_placeholders(version_ids)
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT document_chunks_fts.chunk_id, bm25(document_chunks_fts) AS bm25_score
                FROM document_chunks_fts
                JOIN document_chunks AS chunks ON chunks.chunk_id = document_chunks_fts.chunk_id
                JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
                JOIN document_versions AS versions ON versions.version_id = parents.version_id
                JOIN documents AS documents ON documents.document_id = versions.document_id
                WHERE document_chunks_fts MATCH ?
                  AND versions.owner_id = ?
                  AND versions.version_id IN ("""
                + version_placeholders
                + """)
                  AND versions.status = ? AND versions.is_current = 1 AND versions.retrieval_enabled = 1
                  AND documents.deleted_at IS NULL
                ORDER BY bm25_score ASC LIMIT ?
                """,
                [fts_query, owner_id, *version_ids, DocumentVersionStatus.READY.value, limit],
            ).fetchall()
            ids = [str(row["chunk_id"]) for row in rows]
            records = self._eligible_chunk_rows(connection, owner_id=owner_id, version_ids=version_ids, chunk_ids=ids)
        scores = {str(row["chunk_id"]): float(row["bm25_score"]) for row in rows}
        return [dict(records[chunk_id], bm25_score=scores[chunk_id]) for chunk_id in ids if chunk_id in records]

    def retrieval_parents(
        self, parent_ids: Sequence[str], *, owner_id: str, version_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Load parent text and all children only after repeating owner/version eligibility checks."""

        if not parent_ids or not version_ids:
            return {}
        version_placeholders = self._version_placeholders(version_ids)
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT parents.parent_id, parents.text AS parent_text, parents.logical_heading_path,
                       parents.physical_index, parents.locator, versions.version_id, versions.document_id,
                       documents.title, chunks.chunk_id, chunks.kind, chunks.text, chunks.page_start, chunks.page_end
                FROM document_parents AS parents
                JOIN document_versions AS versions ON versions.version_id = parents.version_id
                JOIN documents AS documents ON documents.document_id = versions.document_id
                LEFT JOIN document_chunks AS chunks ON chunks.parent_id = parents.parent_id
                WHERE parents.parent_id IN ("""
                + ", ".join("?" for _ in parent_ids)
                + """)
                  AND versions.owner_id = ? AND versions.version_id IN ("""
                + version_placeholders
                + """)
                  AND versions.status = ? AND versions.is_current = 1 AND versions.retrieval_enabled = 1
                  AND documents.deleted_at IS NULL
                ORDER BY parents.physical_index ASC, chunks.rowid ASC
                """,
                [*parent_ids, owner_id, *version_ids, DocumentVersionStatus.READY.value],
            ).fetchall()
        parents: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            parent = parents.setdefault(
                str(data["parent_id"]),
                {
                    key: data[key]
                    for key in (
                        "parent_id",
                        "parent_text",
                        "logical_heading_path",
                        "physical_index",
                        "locator",
                        "version_id",
                        "document_id",
                        "title",
                    )
                }
                | {"chunks": []},
            )
            if data["chunk_id"] is not None:
                parent["chunks"].append(
                    {
                        key: data[key]
                        for key in ("chunk_id", "kind", "text", "page_start", "page_end")
                    }
                )
        return parents

    def expired_deleted_documents(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """List candidates for a caller-owned physical cleanup pass without deleting anything."""

        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.config.purge_retention_days)
        with self._transaction() as connection:
            documents = connection.execute(
                "SELECT document_id, owner_id FROM documents WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff.isoformat(),),
            ).fetchall()
            candidates = []
            for document in documents:
                versions = connection.execute(
                    "SELECT version_id, source_path FROM document_versions WHERE document_id = ?",
                    (document["document_id"],),
                ).fetchall()
                chroma_rows = connection.execute(
                    """
                    SELECT chunks.chroma_id FROM document_chunks AS chunks
                    JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
                    WHERE parents.version_id IN (SELECT version_id FROM document_versions WHERE document_id = ?)
                      AND chunks.chroma_id IS NOT NULL
                    """,
                    (document["document_id"],),
                ).fetchall()
                candidates.append(
                    {
                        "document_id": document["document_id"],
                        "owner_id": document["owner_id"],
                        "version_ids": [str(version["version_id"]) for version in versions],
                        "chroma_ids": [str(row["chroma_id"]) for row in chroma_rows],
                    }
                )
        return candidates

    def purge_deleted_document(self, document_id: str, *, owner_id: str, now: datetime | None = None) -> list[str]:
        """Purge one still-expired logical document and return its version IDs for private file cleanup."""

        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.config.purge_retention_days)
        with self._transaction(immediate=True) as connection:
            document = self._require_document(connection, document_id, owner_id)
            if document["deleted_at"] is None or document["deleted_at"] >= cutoff.isoformat():
                return []
            rows = connection.execute(
                "SELECT version_id FROM document_versions WHERE document_id = ?", (document_id,)
            ).fetchall()
            version_ids = [str(row["version_id"]) for row in rows]
            for version_id in version_ids:
                connection.execute("DELETE FROM document_chunks_fts WHERE version_id = ?", (version_id,))
            connection.execute("DELETE FROM documents WHERE document_id = ? AND owner_id = ?", (document_id, owner_id))
        return version_ids

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
