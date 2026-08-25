import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.config import DocumentConfig, EmbeddingsConfig
from src.documents.index import ChromaDocumentVectorStore, DocumentIndexError, DocumentIndexService, VectorMatch, VectorRecord
from src.documents.models import DocumentChunk, DocumentParent, VisionStatus
from src.documents.repository import DocumentRepository
from src.documents.storage import DocumentStorage, StoredUpload
from src.repository import SQLiteRepository
from src.state import new_id


class FakeEmbeddings:
    def __init__(self):
        self.document_inputs: list[list[str]] = []
        self.query_inputs: list[str] = []

    def embed_documents(self, texts):
        self.document_inputs.append(list(texts))
        return [[float(index + 1), 0.5] for index, _ in enumerate(texts)]

    def embed_query(self, text):
        self.query_inputs.append(text)
        return [1.0, 0.5]


class FakeVectorStore:
    def __init__(self):
        self.records: dict[str, VectorRecord] = {}
        self.deleted: list[str] = []
        self.query_matches: list[VectorMatch] | None = None

    def upsert(self, records):
        self.records.update({record.chroma_id: record for record in records})

    def query(self, _embedding, *, owner_id, version_ids, limit):
        if self.query_matches is not None:
            return self.query_matches[:limit]
        return [
            VectorMatch(chunk_id=record.chunk_id, distance=0.1)
            for record in self.records.values()
            if record.metadata["owner_id"] == owner_id
            and record.metadata["version_id"] in version_ids
            and not record.metadata["is_deleted"]
            and record.metadata["retrieval_enabled"]
        ][:limit]

    def update_metadata(self, records):
        for record in records:
            existing = self.records[record.chroma_id]
            self.records[record.chroma_id] = VectorRecord(
                chroma_id=existing.chroma_id,
                chunk_id=existing.chunk_id,
                text=existing.text,
                embedding=existing.embedding,
                metadata=record.metadata,
            )

    def delete(self, chroma_ids):
        self.deleted.extend(chroma_ids)
        for chroma_id in chroma_ids:
            self.records.pop(chroma_id, None)


def _stored_upload(document_id: str, version_id: str, *, name: str = "paper.md") -> StoredUpload:
    return StoredUpload(
        source_filename=name,
        source_media_type="text/markdown",
        source_size=8,
        source_sha256="a" * 64,
        source_path=f"owner/{document_id}/{version_id}/source.md",
    )


def _context(tmp_path):
    config = DocumentConfig(storage_root=str(tmp_path / "private"), purge_retention_days=30)
    core = SQLiteRepository(tmp_path / "research.db")
    owner = core.create_user("index-owner", "password-hash")
    repository = DocumentRepository(core.database_path, config)
    vector_store = FakeVectorStore()
    service = DocumentIndexService(
        repository,
        config,
        EmbeddingsConfig(provider="huggingface", model="test-embeddings"),
        embeddings=FakeEmbeddings(),
        vector_store=vector_store,
    )
    return core, repository, config, owner["user_id"], service, vector_store


def _create_indexable_document(repository, owner_id, *, chunks):
    document_id = new_id("doc")
    version_id = new_id("ver")
    repository.create_document(
        _stored_upload(document_id, version_id), owner_id=owner_id, document_id=document_id, version_id=version_id
    )
    job = repository.claim_next_job("index-worker")
    parent = DocumentParent(
        parent_id=new_id("parent"),
        version_id=version_id,
        logical_heading_path="Paper > Methods",
        physical_index=0,
        text="Full parent context for the paper.",
        locator="pages: 1; section: Paper > Methods",
    )
    models = [
        DocumentChunk(
            chunk_id=chunk_id,
            parent_id=parent.parent_id,
            kind=kind,
            text=text,
            page_start=1,
            page_end=1,
        )
        for chunk_id, kind, text in chunks
    ]
    repository.replace_ingestion_artifacts(job["job_id"], parents=[parent], chunks=models, images=[], vision_status=VisionStatus.NOT_CONFIGURED)
    return document_id, version_id, job, parent, models


def test_index_job_writes_chroma_ids_and_fts_then_lifecycle_syncs_delete_restore(tmp_path):
    core, repository, _config, owner_id, service, vector_store = _context(tmp_path)
    try:
        document_id, version_id, job, _parent, chunks = _create_indexable_document(
            repository,
            owner_id,
            chunks=[(new_id("chunk"), "text", "mitochondrial metabolism improves response")],
        )

        service.index_job(job["job_id"])
        repository.mark_version_ready(document_id, version_id, owner_id=owner_id)
        service.sync_document_state(document_id, owner_id=owner_id)

        connection = sqlite3.connect(repository.database_path)
        try:
            row = connection.execute(
                "SELECT chroma_id, fts_rowid FROM document_chunks WHERE chunk_id = ?", (chunks[0].chunk_id,)
            ).fetchone()
        finally:
            connection.close()
        assert row[0].endswith(chunks[0].chunk_id)
        assert row[1] is not None
        assert repository.bm25_chunks(
            "mitochondrial response", owner_id=owner_id, version_ids=[version_id], limit=10
        )[0]["chunk_id"] == chunks[0].chunk_id
        record = next(iter(vector_store.records.values()))
        assert record.metadata["retrieval_enabled"] is True

        repository.delete_document(document_id, owner_id=owner_id)
        service.sync_document_state(document_id, owner_id=owner_id)
        assert next(iter(vector_store.records.values())).metadata["is_deleted"] is True
        assert repository.bm25_chunks("mitochondrial", owner_id=owner_id, version_ids=[version_id], limit=10) == []

        repository.restore_document(document_id, owner_id=owner_id)
        service.sync_document_state(document_id, owner_id=owner_id)
        restored = next(iter(vector_store.records.values())).metadata
        assert restored["is_deleted"] is False
        assert restored["retrieval_enabled"] is True
    finally:
        core.close()


def test_index_compensates_chroma_when_sqlite_mapping_write_fails(tmp_path, monkeypatch):
    core, repository, _config, owner_id, service, vector_store = _context(tmp_path)
    try:
        _document_id, _version_id, job, _parent, _chunks = _create_indexable_document(
            repository, owner_id, chunks=[(new_id("chunk"), "text", "recoverable index failure")]
        )

        def fail_mapping(*_args, **_kwargs):
            raise RuntimeError("SQLite unavailable")

        monkeypatch.setattr(repository, "record_chunk_index_entries", fail_mapping)
        with pytest.raises(DocumentIndexError, match="FTS/mapping"):
            service.index_job(job["job_id"])
        assert vector_store.records == {}
        assert vector_store.deleted
    finally:
        core.close()


def test_expired_purge_deletes_vector_database_and_private_files(tmp_path):
    core, repository, config, owner_id, service, vector_store = _context(tmp_path)
    storage = DocumentStorage(config)
    try:
        document_id, version_id, job, _parent, _chunks = _create_indexable_document(
            repository, owner_id, chunks=[(new_id("chunk"), "text", "expired content")]
        )
        version_directory = storage._version_directory(owner_id, document_id, version_id)
        version_directory.mkdir(parents=True)
        (version_directory / "source.md").write_text("expired content", encoding="utf-8")
        service.index_job(job["job_id"])
        repository.mark_version_ready(document_id, version_id, owner_id=owner_id)
        repository.delete_document(document_id, owner_id=owner_id)
        cutoff_time = datetime.now(timezone.utc) + timedelta(days=config.purge_retention_days + 1)

        assert service.purge_expired_documents(storage, now=cutoff_time) == 1
        assert vector_store.records == {}
        assert not version_directory.exists()
        with pytest.raises(Exception):
            repository.get_document(document_id, owner_id=owner_id)
    finally:
        core.close()


def test_chroma_adapter_filters_soft_deleted_metadata_and_deletes_physically(tmp_path):
    store = ChromaDocumentVectorStore(
        DocumentConfig(chroma_persist_dir=str(tmp_path / "chroma"), chroma_collection="document_index_test")
    )
    record = VectorRecord(
        chroma_id="document_chunk_chunk_1",
        chunk_id="chunk_1",
        text="private document text",
        embedding=[1.0, 0.0],
        metadata={
            "chunk_id": "chunk_1",
            "owner_id": "owner_a",
            "document_id": "doc_1",
            "version_id": "ver_1",
            "parent_id": "parent_1",
            "kind": "text",
            "is_deleted": False,
            "retrieval_enabled": True,
        },
    )

    store.upsert([record])
    assert store.query([1.0, 0.0], owner_id="owner_a", version_ids=["ver_1"], limit=5)[0].chunk_id == "chunk_1"

    deleted = VectorRecord(
        chroma_id=record.chroma_id,
        chunk_id=record.chunk_id,
        text="",
        embedding=[],
        metadata={**record.metadata, "is_deleted": True, "retrieval_enabled": False},
    )
    store.update_metadata([deleted])
    assert store.query([1.0, 0.0], owner_id="owner_a", version_ids=["ver_1"], limit=5) == []
    store.delete([record.chroma_id])
