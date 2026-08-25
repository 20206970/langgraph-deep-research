"""Scope-bound private document retrieval for one research run."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from langchain_core.tools import tool

from src.documents.retrieval import DocumentRetrievalResult, DocumentRetrievalService, RetrievedParent
from src.state import DocumentScope, utc_now


def _document_source_id(parent: RetrievedParent) -> str:
    """Create a stable opaque source ID without exposing document or version IDs."""

    identity = f"{parent.version_id}\x00{parent.parent_id}\x00{parent.matched_chunk.chunk_id}"
    return f"src_doc_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _page_range(parent: RetrievedParent) -> tuple[int | None, int | None]:
    pages = [
        page
        for chunk in parent.context_chunks
        for page in (chunk.page_start, chunk.page_end)
        if page is not None
    ]
    return (min(pages), max(pages)) if pages else (None, None)


def _document_locator(parent: RetrievedParent) -> str:
    """Format an end-user citation locator without private storage paths."""

    page_start, page_end = _page_range(parent)
    heading = parent.logical_heading_path.strip()
    parts: list[str] = []
    if page_start is not None:
        pages = f"第 {page_start} 页" if page_start == page_end else f"第 {page_start}-{page_end} 页"
        parts.append(pages)
    if heading:
        parts.append(f"章节：{heading}")
    return "；".join(parts) or "文档正文"


def _evidence_excerpt(parent: RetrievedParent, *, max_length: int = 1_500) -> str:
    """Return a bounded local context and retain the non-source vision label."""

    parts: list[str] = []
    for chunk in parent.context_chunks:
        text = chunk.text.strip()
        if not text:
            continue
        if chunk.kind == "vision":
            parts.append(f"[视觉增强，非原文] {text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)[:max_length]


def _source_record(parent: RetrievedParent) -> dict[str, Any]:
    excerpt = _evidence_excerpt(parent)
    return {
        "source_id": _document_source_id(parent),
        "source_type": "private_document",
        "provider": "private_document_rag",
        "canonical_url": None,
        "title": parent.title[:500],
        "retrieved_at": utc_now(),
        "content_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "evidence_excerpt": excerpt,
        "locator": _document_locator(parent),
    }


def _retrieval_metadata(
    result: DocumentRetrievalResult | None,
    *,
    scope_version_count: int,
    reranker_model: str,
    latency_ms: int,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Expose only low-cardinality diagnostics to events and optional tracing."""

    return {
        "scope_version_count": scope_version_count,
        "parent_count": len(result.parents) if result else 0,
        "vector_candidate_count": result.vector_candidate_count if result else 0,
        "bm25_candidate_count": result.bm25_candidate_count if result else 0,
        "vector_status": result.vector_status if result else "failed",
        "reranker_status": result.reranker_status if result else "not_applicable",
        "reranker_model": reranker_model,
        "latency_ms": latency_ms,
        "error_code": error_code,
    }


def create_document_search_tool(
    retrieval_service: DocumentRetrievalService,
    *,
    owner_id: str,
    document_scope: DocumentScope,
):
    """Create one private-document tool whose authorization scope cannot be overridden.

    The closure keeps an immutable owner/version tuple.  Deliberately do not add
    owner or version parameters to the tool schema: ReAct arguments are model
    generated and must not expand the server-validated run scope.
    """

    scope = DocumentScope.model_validate(document_scope).model_copy(deep=True)
    allowed_version_ids = tuple(scope.version_ids)
    bound_owner_id = str(owner_id)
    reranker_model = retrieval_service.reranker.config.model

    @tool("search_private_documents")
    def search_private_documents(query: str) -> str:
        """Search only the private documents selected for this research run.

        Use the returned source_id for citations. Text marked as visual enhancement
        is model-generated context rather than a quotation from the source paper.
        """

        started_at = time.perf_counter()
        try:
            result = retrieval_service.search(
                query,
                owner_id=bound_owner_id,
                version_ids=allowed_version_ids,
            )
            metadata = _retrieval_metadata(
                result,
                scope_version_count=len(allowed_version_ids),
                reranker_model=reranker_model,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
            )
            payload = {
                "provider": "private_document_rag",
                "source_type": "private_document",
                "results": [_source_record(parent) for parent in result.parents],
                "retrieval": metadata,
            }
        except Exception:
            payload = {
                "provider": "private_document_rag",
                "source_type": "private_document",
                "results": [],
                "retrieval": _retrieval_metadata(
                    None,
                    scope_version_count=len(allowed_version_ids),
                    reranker_model=reranker_model,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                    error_code="DOCUMENT_RETRIEVAL_FAILED",
                ),
            }
        return json.dumps(payload, ensure_ascii=False)

    return search_private_documents
