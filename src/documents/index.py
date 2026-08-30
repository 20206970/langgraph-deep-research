"""Independent Chroma + FTS indexing for private document chunks."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from src.config import DocumentConfig, EmbeddingsConfig
from src.memory.long_term import create_embeddings

from .repository import DocumentRepository
from .storage import DocumentStorage


class DocumentIndexError(RuntimeError):
    """A document vector/FTS index operation failed and may be retried by the ingestion job."""


@dataclass(frozen=True)
class VectorRecord:
    chroma_id: str
    chunk_id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VectorMatch:
    chunk_id: str
    distance: float


class DocumentVectorStore(Protocol):
    def upsert(self, records: Sequence[VectorRecord]) -> None:
        """Store or replace vector records."""

    def query(
        self, embedding: list[float], *, owner_id: str, version_ids: Sequence[str], limit: int
    ) -> list[VectorMatch]:
        """Return pre-filtered vector candidates in ascending distance order."""

    def update_metadata(self, records: Sequence[VectorRecord]) -> None:
        """Update lifecycle metadata without recalculating embeddings."""

    def delete(self, chroma_ids: Sequence[str]) -> None:
        """Permanently remove vector records."""


class ChromaDocumentVectorStore:
    """Thin Chroma adapter kept separate from authorization and SQLite lifecycle decisions."""

    def __init__(self, config: DocumentConfig):
        try:
            import chromadb
        except ImportError as error:
            raise DocumentIndexError("chromadb is not installed") from error
        Path(config.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=config.chroma_persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=config.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _where(owner_id: str, version_ids: Sequence[str]) -> dict[str, Any]:
        conditions: list[dict[str, Any]] = [
            {"owner_id": owner_id},
            {"is_deleted": False},
            {"retrieval_enabled": True},
        ]
        if len(version_ids) == 1:
            conditions.append({"version_id": version_ids[0]})
        else:
            conditions.append({"version_id": {"$in": list(version_ids)}})
        return {"$and": conditions}

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        try:
            self._collection.upsert(
                ids=[record.chroma_id for record in records],
                embeddings=[record.embedding for record in records],
                documents=[record.text for record in records],
                metadatas=[record.metadata for record in records],
            )
        except Exception as error:
            raise DocumentIndexError("Chroma vector upsert failed") from error

    def query(
        self, embedding: list[float], *, owner_id: str, version_ids: Sequence[str], limit: int
    ) -> list[VectorMatch]:
        if not version_ids:
            return []
        try:
            response = self._collection.query(
                query_embeddings=[embedding],
                n_results=limit,
                where=self._where(owner_id, version_ids),
                include=["metadatas", "distances"],
            )
        except Exception as error:
            raise DocumentIndexError("Chroma vector query failed") from error
        metadata = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]
        return [
            VectorMatch(chunk_id=str(item["chunk_id"]), distance=float(distance))
            for item, distance in zip(metadata, distances)
            if item and item.get("chunk_id")
        ]

    def update_metadata(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        try:
            self._collection.update(
                ids=[record.chroma_id for record in records],
                metadatas=[record.metadata for record in records],
            )
        except Exception as error:
            raise DocumentIndexError("Chroma lifecycle metadata update failed") from error

    def delete(self, chroma_ids: Sequence[str]) -> None:
        if not chroma_ids:
            return
        try:
            self._collection.delete(ids=list(chroma_ids))
        except Exception as error:
            raise DocumentIndexError("Chroma vector deletion failed") from error

    def close(self) -> None:
        """Release the persistent Chroma client so temporary stores can be removed on Windows."""

        close = getattr(self._client, "close", None)
        if callable(close):
            close()


class DocumentIndexService:
    """Coordinates vector writes with SQLite FTS/mapping writes and lifecycle metadata updates."""

    def __init__(
        self,
        repository: DocumentRepository,
        document_config: DocumentConfig,
        embeddings_config: EmbeddingsConfig,
        *,
        embeddings: Any | None = None,
        vector_store: DocumentVectorStore | None = None,
    ):
        self.repository = repository
        self.document_config = document_config
        self.embeddings_config = embeddings_config
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._lazy_init_lock = threading.Lock()

    @property
    def embeddings(self):
        if self._embeddings is None:
            # Parallel research tasks may reach this first-use path concurrently;
            # an unsynchronized load builds duplicate GPU models in every racing thread.
            with self._lazy_init_lock:
                if self._embeddings is None:
                    try:
                        self._embeddings = create_embeddings(self.embeddings_config)
                    except Exception as error:
                        raise DocumentIndexError("document embedding model initialization failed") from error
        return self._embeddings

    @property
    def vector_store(self) -> DocumentVectorStore:
        if self._vector_store is None:
            with self._lazy_init_lock:
                if self._vector_store is None:
                    self._vector_store = ChromaDocumentVectorStore(self.document_config)
        return self._vector_store

    @staticmethod
    def _chroma_id(chunk_id: str) -> str:
        return f"document_chunk_{chunk_id}"

    @staticmethod
    def _metadata(record: dict[str, Any], *, is_deleted: bool, retrieval_enabled: bool) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "chunk_id": str(record["chunk_id"]),
            "owner_id": str(record["owner_id"]),
            "document_id": str(record["document_id"]),
            "version_id": str(record["version_id"]),
            "parent_id": str(record["parent_id"]),
            "kind": str(record["kind"]),
            "is_deleted": is_deleted,
            "retrieval_enabled": retrieval_enabled,
        }
        if record.get("page_start") is not None:
            metadata["page_start"] = int(record["page_start"])
        if record.get("page_end") is not None:
            metadata["page_end"] = int(record["page_end"])
        return metadata

    def _fingerprint(self, rows: Sequence[dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        digest.update(b"document-index-v1\x00")
        digest.update(self.embeddings_config.provider.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(self.embeddings_config.model.encode("utf-8"))
        for row in rows:
            digest.update(str(row["chunk_id"]).encode("utf-8"))
            digest.update(b"\x00")
        return f"document-index:v1:{digest.hexdigest()[:32]}"

    def index_job(self, job_id: str) -> None:
        """Index every chunk from a leased job; SQLite failure compensates the fresh Chroma upsert."""

        rows = self.repository.indexable_chunks_for_job(job_id)
        if not rows:
            raise DocumentIndexError("document contains no chunks to index")
        try:
            embeddings = self.embeddings.embed_documents([str(row["text"]) for row in rows])
            if len(embeddings) != len(rows):
                raise ValueError("embedding provider returned an unexpected vector count")
            records = [
                VectorRecord(
                    chroma_id=self._chroma_id(str(row["chunk_id"])),
                    chunk_id=str(row["chunk_id"]),
                    text=str(row["text"]),
                    embedding=[float(value) for value in embedding],
                    # The worker promotes this version immediately after the handler returns.
                    # SQLite re-authorization still blocks it until that promotion succeeds.
                    metadata=self._metadata(row, is_deleted=False, retrieval_enabled=True),
                )
                for row, embedding in zip(rows, embeddings)
            ]
            self.vector_store.upsert(records)
        except DocumentIndexError:
            raise
        except Exception as error:
            raise DocumentIndexError("document vector indexing failed") from error
        try:
            self.repository.record_chunk_index_entries(
                job_id,
                chroma_ids={record.chunk_id: record.chroma_id for record in records},
                index_fingerprint=self._fingerprint(rows),
            )
        except Exception as error:
            try:
                self.vector_store.delete([record.chroma_id for record in records])
            except DocumentIndexError:
                pass
            raise DocumentIndexError("document FTS/mapping indexing failed") from error

    def vector_candidates(
        self, query: str, *, owner_id: str, version_ids: Sequence[str], limit: int
    ) -> list[VectorMatch]:
        if not version_ids:
            return []
        try:
            embedding = self.embeddings.embed_query(query)
            return self.vector_store.query(
                [float(value) for value in embedding], owner_id=owner_id, version_ids=version_ids, limit=limit
            )
        except DocumentIndexError:
            raise
        except Exception as error:
            raise DocumentIndexError("document vector retrieval failed") from error

    def sync_document_state(self, document_id: str, *, owner_id: str) -> None:
        """Synchronize Chroma soft-delete/retrieval flags after delete, restore, or version promotion."""

        states = self.repository.document_vector_states(document_id, owner_id=owner_id)
        if not states:
            return
        records = [
            VectorRecord(
                chroma_id=str(state["chroma_id"]),
                chunk_id=str(state["chunk_id"]),
                text="",
                embedding=[],
                metadata=self._metadata(
                    state,
                    is_deleted=bool(state["is_deleted"]),
                    retrieval_enabled=bool(state["retrieval_enabled"]),
                ),
            )
            for state in states
        ]
        self.vector_store.update_metadata(records)

    def purge_expired_documents(self, storage: DocumentStorage, *, now: datetime | None = None) -> int:
        """Physically remove expired deleted documents; stale vectors remain SQLite-filtered on a retryable failure."""

        removed = 0
        for candidate in self.repository.expired_deleted_documents(now=now):
            # Delete from the external index first. If the document is restored between the
            # candidate scan and metadata purge, the authoritative state is written back below.
            self.vector_store.delete(candidate["chroma_ids"])
            version_ids = self.repository.purge_deleted_document(
                str(candidate["document_id"]), owner_id=str(candidate["owner_id"]), now=now
            )
            if not version_ids:
                self.sync_document_state(str(candidate["document_id"]), owner_id=str(candidate["owner_id"]))
                continue
            for version_id in version_ids:
                storage.remove_version(
                    owner_id=str(candidate["owner_id"]),
                    document_id=str(candidate["document_id"]),
                    version_id=version_id,
                )
            removed += 1
        return removed
