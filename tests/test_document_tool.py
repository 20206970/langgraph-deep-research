import json

from src.documents.retrieval import DocumentRetrievalResult, RetrievedChunk, RetrievedParent
from src.state import DocumentScope
from src.tools.documents import create_document_search_tool


class _FakeReranker:
    class config:
        model = "test-reranker"


class _FakeRetrievalService:
    def __init__(self, *, fail: bool = False):
        self.reranker = _FakeReranker()
        self.fail = fail
        self.calls = []

    def search(self, query, *, owner_id, version_ids):
        self.calls.append((query, owner_id, version_ids))
        if self.fail:
            raise RuntimeError("private storage path must not be returned")
        text = RetrievedChunk("chunk_text", "text", "Matched paper evidence.", 2, 3, 0.1, 1, 1)
        vision = RetrievedChunk("chunk_vision", "vision", "A chart trends upward.", 3, 3, 0.0, None, None)
        parent = RetrievedParent(
            parent_id="parent_1",
            document_id="doc_not_exposed",
            version_id="version_allowed",
            title="Private Paper",
            logical_heading_path="Paper > Results",
            locator="ignored internal locator",
            text="Parent text is only used for reranking.",
            matched_chunk=text,
            context_chunks=(text, vision),
            rrf_score=0.1,
            reranker_score=0.8,
        )
        return DocumentRetrievalResult((parent,), "applied", "degraded", 3, 2)


def test_document_tool_freezes_scope_and_emits_source_compatible_records():
    service = _FakeRetrievalService()
    scope = DocumentScope(selection_mode="explicit", version_ids=["version_allowed"])
    tool = create_document_search_tool(service, owner_id="owner_a", document_scope=scope)
    scope.version_ids.append("version_not_allowed")

    payload = json.loads(tool.invoke({"query": "paper evidence"}))

    assert service.calls == [("paper evidence", "owner_a", ("version_allowed",))]
    assert set(tool.args) == {"query"}
    assert payload["retrieval"] == {
        "scope_version_count": 1,
        "parent_count": 1,
        "vector_candidate_count": 3,
        "bm25_candidate_count": 2,
        "vector_status": "applied",
        "reranker_status": "degraded",
        "reranker_model": "test-reranker",
        "latency_ms": payload["retrieval"]["latency_ms"],
        "error_code": None,
    }
    source = payload["results"][0]
    assert source["source_type"] == "private_document"
    assert source["title"] == "Private Paper"
    assert source["locator"] == "第 2-3 页；章节：Paper > Results"
    assert "[视觉增强，非原文]" in source["evidence_excerpt"]
    assert "doc_not_exposed" not in json.dumps(payload, ensure_ascii=False)
    assert "version_allowed" not in json.dumps(payload, ensure_ascii=False)


def test_document_tool_returns_only_a_safe_error_code_when_retrieval_fails():
    tool = create_document_search_tool(
        _FakeRetrievalService(fail=True),
        owner_id="owner_a",
        document_scope=DocumentScope(selection_mode="explicit", version_ids=["version_allowed"]),
    )

    payload = json.loads(tool.invoke({"query": "paper evidence"}))

    assert payload["results"] == []
    assert payload["retrieval"]["error_code"] == "DOCUMENT_RETRIEVAL_FAILED"
    assert "private storage path" not in json.dumps(payload, ensure_ascii=False)
