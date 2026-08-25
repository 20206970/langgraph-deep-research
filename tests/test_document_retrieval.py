from dataclasses import dataclass

from src.config import DocumentConfig, DocumentRetrievalConfig, EmbeddingsConfig, RerankerConfig
from src.documents.index import DocumentIndexService, VectorMatch, VectorRecord
from src.documents.models import DocumentChunk, DocumentParent, VisionStatus
from src.documents.reranker import DocumentRerankerService
from src.documents.repository import DocumentRepository
from src.documents.retrieval import DocumentRetrievalService, reciprocal_rank_fusion
from src.documents.storage import StoredUpload
from src.repository import SQLiteRepository
from src.state import new_id


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [[float(index + 1), 1.0] for index, _ in enumerate(texts)]

    def embed_query(self, _query):
        return [1.0, 1.0]


class FakeVectorStore:
    def __init__(self):
        self.records: dict[str, VectorRecord] = {}
        self.matches: list[VectorMatch] = []

    def upsert(self, records):
        self.records.update({record.chroma_id: record for record in records})

    def query(self, _embedding, *, owner_id, version_ids, limit):
        return self.matches[:limit]

    def update_metadata(self, records):
        for record in records:
            original = self.records[record.chroma_id]
            self.records[record.chroma_id] = VectorRecord(
                original.chroma_id, original.chunk_id, original.text, original.embedding, record.metadata
            )

    def delete(self, chroma_ids):
        for chroma_id in chroma_ids:
            self.records.pop(chroma_id, None)


class FakeReranker:
    def __init__(self, scores=None, fail=False):
        self.scores = scores or []
        self.fail = fail
        self.documents: list[str] = []

    def score(self, _query, documents):
        self.documents = list(documents)
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.scores


def _upload(document_id, version_id):
    return StoredUpload(
        source_filename="paper.md",
        source_media_type="text/markdown",
        source_size=10,
        source_sha256="b" * 64,
        source_path=f"owner/{document_id}/{version_id}/source.md",
    )


def _add_ready_document(repository, owner_id, parent_specs):
    document_id, version_id = new_id("doc"), new_id("ver")
    repository.create_document(_upload(document_id, version_id), owner_id=owner_id, document_id=document_id, version_id=version_id)
    job = repository.claim_next_job("retrieval-worker")
    parents, chunks = [], []
    for index, (heading, parent_text, child_specs) in enumerate(parent_specs):
        parent = DocumentParent(
            parent_id=new_id("parent"),
            version_id=version_id,
            logical_heading_path=heading,
            physical_index=index,
            text=parent_text,
            locator=f"pages: {index + 1}; section: {heading}",
        )
        parents.append(parent)
        for kind, text in child_specs:
            chunks.append(
                DocumentChunk(
                    chunk_id=new_id("chunk"),
                    parent_id=parent.parent_id,
                    kind=kind,
                    text=text,
                    page_start=index + 1,
                    page_end=index + 1,
                )
            )
    repository.replace_ingestion_artifacts(job["job_id"], parents=parents, chunks=chunks, images=[], vision_status=VisionStatus.NOT_CONFIGURED)
    return document_id, version_id, job, parents, chunks


def _context(tmp_path):
    config = DocumentConfig(storage_root=str(tmp_path / "private"))
    core = SQLiteRepository(tmp_path / "research.db")
    owner_a = core.create_user("retrieval-a", "password-hash")["user_id"]
    owner_b = core.create_user("retrieval-b", "password-hash")["user_id"]
    repository = DocumentRepository(core.database_path, config)
    vector = FakeVectorStore()
    index = DocumentIndexService(
        repository,
        config,
        EmbeddingsConfig(provider="huggingface", model="test"),
        embeddings=FakeEmbeddings(),
        vector_store=vector,
    )
    return core, repository, owner_a, owner_b, index, vector


def test_rrf_and_parent_reranking_use_child_candidates_but_score_full_parent_with_visual_context(tmp_path):
    core, repository, owner_a, _owner_b, index, vector = _context(tmp_path)
    try:
        document_id, version_id, job, parents, chunks = _add_ready_document(
            repository,
            owner_a,
            [
                ("Paper > Biomarker", "Parent one discusses the alpha biomarker.", [("text", "alpha biomarker response")]),
                (
                    "Paper > Treatment",
                    "Parent two discusses treatment results.",
                    [("text", "gamma treatment response"), ("vision", "Visual enhancement (non-source): treatment curve rises.")],
                ),
            ],
        )
        index.index_job(job["job_id"])
        repository.mark_version_ready(document_id, version_id, owner_id=owner_a)
        index.sync_document_state(document_id, owner_id=owner_a)
        vector.matches = [VectorMatch(chunks[1].chunk_id, 0.1), VectorMatch(chunks[0].chunk_id, 0.2)]
        reranker_model = FakeReranker(scores=[0.1, 0.9])
        service = DocumentRetrievalService(
            repository,
            index,
            DocumentRetrievalConfig(vector_top_k=5, bm25_top_k=5, parent_candidate_k=5, neighbor_window=1),
            DocumentRerankerService(RerankerConfig(top_k=5), reranker=reranker_model),
        )

        result = service.search("alpha biomarker", owner_id=owner_a, version_ids=[version_id])

        assert result.reranker_status == "applied"
        assert result.vector_status == "applied"
        assert len(result.parents) == 2
        assert result.parents[0].parent_id == parents[1].parent_id
        assert result.parents[0].matched_chunk.chunk_id == chunks[1].chunk_id
        assert "[Visual enhancement, non-source]" in reranker_model.documents[1]
        assert any(chunk.kind == "vision" for chunk in result.parents[0].context_chunks)
        assert result.parents[1].matched_chunk.bm25_rank == 1
    finally:
        core.close()


def test_reranker_failure_preserves_rrf_order_and_sqlite_filters_cross_owner_and_deleted_versions(tmp_path):
    core, repository, owner_a, owner_b, index, vector = _context(tmp_path)
    try:
        doc_a, version_a, job_a, parents_a, chunks_a = _add_ready_document(
            repository,
            owner_a,
            [
                ("Paper > Alpha", "alpha parent", [("text", "alpha evidence")]),
                ("Paper > Beta", "beta parent", [("text", "beta evidence")]),
            ],
        )
        _doc_b, version_b, job_b, _parents_b, chunks_b = _add_ready_document(
            repository, owner_b, [("Private", "private parent", [("text", "alpha private evidence")])]
        )
        index.index_job(job_a["job_id"])
        repository.mark_version_ready(doc_a, version_a, owner_id=owner_a)
        index.sync_document_state(doc_a, owner_id=owner_a)
        index.index_job(job_b["job_id"])
        repository.mark_version_ready(_doc_b, version_b, owner_id=owner_b)
        index.sync_document_state(_doc_b, owner_id=owner_b)
        vector.matches = [
            VectorMatch(chunks_b[0].chunk_id, 0.01),
            VectorMatch(chunks_a[1].chunk_id, 0.1),
            VectorMatch(chunks_a[0].chunk_id, 0.2),
        ]
        service = DocumentRetrievalService(
            repository,
            index,
            DocumentRetrievalConfig(vector_top_k=5, bm25_top_k=5, parent_candidate_k=5),
            DocumentRerankerService(RerankerConfig(top_k=5), reranker=FakeReranker(fail=True)),
        )

        result = service.search("alpha", owner_id=owner_a, version_ids=[version_a])

        assert result.reranker_status == "degraded"
        assert all(parent.version_id == version_a for parent in result.parents)
        assert result.parents[0].parent_id == parents_a[0].parent_id
        repository.delete_document(doc_a, owner_id=owner_a)
        index.sync_document_state(doc_a, owner_id=owner_a)
        assert service.search("alpha", owner_id=owner_a, version_ids=[version_a]).parents == ()
    finally:
        core.close()


def test_reciprocal_rank_fusion_adds_dual_recall_signal():
    scores = reciprocal_rank_fusion(["vector-first", "shared"], ["shared", "bm25-only"], rrf_k=60)

    assert scores["shared"][0] > scores["vector-first"][0]
    assert scores["shared"][1:] == (2, 1)
