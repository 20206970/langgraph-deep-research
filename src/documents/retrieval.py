"""Authorized hybrid retrieval: child-vector/FTS candidates, RRF, parent aggregation, then reranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.config import DocumentRetrievalConfig

from .index import DocumentIndexError, DocumentIndexService
from .reranker import DocumentRerankerService
from .repository import DocumentRepository


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    kind: str
    text: str
    page_start: int | None
    page_end: int | None
    rrf_score: float
    vector_rank: int | None
    bm25_rank: int | None


@dataclass(frozen=True)
class RetrievedParent:
    parent_id: str
    document_id: str
    version_id: str
    title: str
    logical_heading_path: str
    locator: str | None
    text: str
    matched_chunk: RetrievedChunk
    context_chunks: tuple[RetrievedChunk, ...]
    rrf_score: float
    reranker_score: float | None


@dataclass(frozen=True)
class DocumentRetrievalResult:
    parents: tuple[RetrievedParent, ...]
    vector_status: str
    reranker_status: str
    vector_candidate_count: int
    bm25_candidate_count: int


def reciprocal_rank_fusion(
    vector_ids: Sequence[str], bm25_ids: Sequence[str], *, rrf_k: int
) -> dict[str, tuple[float, int | None, int | None]]:
    """Fuse rank-only candidates while retaining each source rank for diagnosis and tests."""

    scores: dict[str, list[float | int | None]] = {}
    for rank, chunk_id in enumerate(vector_ids, start=1):
        entry = scores.setdefault(chunk_id, [0.0, None, None])
        entry[0] = float(entry[0]) + 1.0 / (rrf_k + rank)
        entry[1] = rank
    for rank, chunk_id in enumerate(bm25_ids, start=1):
        entry = scores.setdefault(chunk_id, [0.0, None, None])
        entry[0] = float(entry[0]) + 1.0 / (rrf_k + rank)
        entry[2] = rank
    return {chunk_id: (float(value[0]), value[1], value[2]) for chunk_id, value in scores.items()}


class DocumentRetrievalService:
    """The only retrieval entry point. Every candidate is re-authorized by ``DocumentRepository``."""

    def __init__(
        self,
        repository: DocumentRepository,
        index_service: DocumentIndexService,
        config: DocumentRetrievalConfig,
        reranker: DocumentRerankerService,
    ):
        self.repository = repository
        self.index_service = index_service
        self.config = config
        self.reranker = reranker

    @staticmethod
    def _reranker_text(parent: dict) -> str:
        vision = [str(chunk["text"]) for chunk in parent["chunks"] if chunk["kind"] == "vision"]
        if not vision:
            return str(parent["parent_text"])
        return str(parent["parent_text"]) + "\n\n[Visual enhancement, non-source]\n" + "\n\n".join(vision)

    def search(self, query: str, *, owner_id: str, version_ids: Sequence[str]) -> DocumentRetrievalResult:
        """Return only current, owned, non-deleted versions within an already resolved immutable scope."""

        normalized_query = query.strip()
        if not normalized_query or not version_ids:
            return DocumentRetrievalResult((), "not_applicable", "not_applicable", 0, 0)

        vector_status = "applied"
        try:
            raw_vector = self.index_service.vector_candidates(
                normalized_query,
                owner_id=owner_id,
                version_ids=version_ids,
                limit=self.config.vector_top_k,
            )
        except DocumentIndexError:
            raw_vector = []
            vector_status = "degraded"
        vector_ids = [match.chunk_id for match in raw_vector]
        eligible_vector = self.repository.eligible_chunks(vector_ids, owner_id=owner_id, version_ids=version_ids)
        eligible_vector_ids = [str(record["chunk_id"]) for record in eligible_vector]

        bm25_records = self.repository.bm25_chunks(
            normalized_query,
            owner_id=owner_id,
            version_ids=version_ids,
            limit=self.config.bm25_top_k,
        )
        bm25_ids = [str(record["chunk_id"]) for record in bm25_records]
        fused = reciprocal_rank_fusion(eligible_vector_ids, bm25_ids, rrf_k=self.config.rrf_k)
        if not fused:
            return DocumentRetrievalResult((), vector_status, "not_applicable", len(eligible_vector_ids), len(bm25_ids))

        records_by_id = {str(record["chunk_id"]): record for record in [*eligible_vector, *bm25_records]}
        parent_best: dict[str, tuple[str, float, int | None, int | None]] = {}
        for chunk_id, (score, vector_rank, bm25_rank) in fused.items():
            record = records_by_id.get(chunk_id)
            if record is None:
                continue
            parent_id = str(record["parent_id"])
            current = parent_best.get(parent_id)
            if current is None or score > current[1]:
                parent_best[parent_id] = (chunk_id, score, vector_rank, bm25_rank)
        ranked_parent_ids = [
            parent_id
            for parent_id, _ in sorted(parent_best.items(), key=lambda item: item[1][1], reverse=True)[: self.config.parent_candidate_k]
        ]
        parents = self.repository.retrieval_parents(ranked_parent_ids, owner_id=owner_id, version_ids=version_ids)
        ordered = [parent_id for parent_id in ranked_parent_ids if parent_id in parents]
        outcome = self.reranker.rerank(
            normalized_query,
            [(parent_id, self._reranker_text(parents[parent_id])) for parent_id in ordered],
        )

        results: list[RetrievedParent] = []
        for parent_id in outcome.order:
            parent = parents[parent_id]
            chunk_id, rrf_score, vector_rank, bm25_rank = parent_best[parent_id]
            chunks = parent["chunks"]
            match_index = next(index for index, chunk in enumerate(chunks) if chunk["chunk_id"] == chunk_id)
            lower = max(0, match_index - self.config.neighbor_window)
            upper = min(len(chunks), match_index + self.config.neighbor_window + 1)
            context = []
            for chunk in chunks[lower:upper]:
                child_score, child_vector_rank, child_bm25_rank = fused.get(chunk["chunk_id"], (0.0, None, None))
                context.append(
                    RetrievedChunk(
                        chunk_id=str(chunk["chunk_id"]),
                        kind=str(chunk["kind"]),
                        text=str(chunk["text"]),
                        page_start=chunk["page_start"],
                        page_end=chunk["page_end"],
                        rrf_score=child_score,
                        vector_rank=child_vector_rank,
                        bm25_rank=child_bm25_rank,
                    )
                )
            matched = next(chunk for chunk in context if chunk.chunk_id == chunk_id)
            results.append(
                RetrievedParent(
                    parent_id=parent_id,
                    document_id=str(parent["document_id"]),
                    version_id=str(parent["version_id"]),
                    title=str(parent["title"]),
                    logical_heading_path=str(parent["logical_heading_path"]),
                    locator=parent["locator"],
                    text=str(parent["parent_text"]),
                    matched_chunk=matched,
                    context_chunks=tuple(context),
                    rrf_score=rrf_score,
                    reranker_score=outcome.scores.get(parent_id),
                )
            )
        return DocumentRetrievalResult(
            parents=tuple(results),
            vector_status=vector_status,
            reranker_status=outcome.status,
            vector_candidate_count=len(eligible_vector_ids),
            bm25_candidate_count=len(bm25_ids),
        )
