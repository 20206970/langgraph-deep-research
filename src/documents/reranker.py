"""Parent-level reranking with an explicit RRF fallback when a local model is unavailable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from src.config import RerankerConfig


class RerankerError(RuntimeError):
    """The configured reranker could not be loaded or score the requested parents."""


class ParentReranker(Protocol):
    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """Return one relevance score for each input parent in matching order."""


class FlagEmbeddingReranker:
    """Lazy adapter for the configured BGE reranker; no alternative model is selected on failure."""

    def __init__(self, config: RerankerConfig):
        self.config = config
        self._model = None

    @property
    def model(self):
        if self._model is None:
            try:
                from FlagEmbedding import FlagReranker
            except ImportError as error:
                raise RerankerError("FlagEmbedding is not installed") from error
            try:
                self._model = FlagReranker(
                    self.config.model,
                    use_fp16=self.config.device.lower() not in {"cpu", "none"},
                )
            except Exception as error:
                raise RerankerError("configured reranker model could not be initialized") from error
        return self._model

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        try:
            scores = self.model.compute_score(
                [[query, document] for document in documents],
                batch_size=self.config.batch_size,
                max_length=self.config.max_length,
            )
        except RerankerError:
            raise
        except Exception as error:
            raise RerankerError("configured reranker inference failed") from error
        if isinstance(scores, (float, int)):
            scores = [scores]
        if len(scores) != len(documents):
            raise RerankerError("configured reranker returned an unexpected score count")
        return [float(score) for score in scores]


@dataclass(frozen=True)
class RerankOutcome:
    order: tuple[str, ...]
    scores: dict[str, float]
    status: str


class DocumentRerankerService:
    """Score parent candidates or return their RRF order with a visible degraded status."""

    def __init__(self, config: RerankerConfig, *, reranker: ParentReranker | None = None):
        self.config = config
        self.reranker = reranker or FlagEmbeddingReranker(config)

    def rerank(self, query: str, candidates: Sequence[tuple[str, str]]) -> RerankOutcome:
        if not candidates:
            return RerankOutcome(order=(), scores={}, status="not_applicable")
        limited = list(candidates[: self.config.top_k])
        try:
            values = self.reranker.score(query, [document for _, document in limited])
        except Exception:
            return RerankOutcome(order=tuple(identifier for identifier, _ in candidates), scores={}, status="degraded")
        ranked = sorted(zip(limited, values), key=lambda item: item[1], reverse=True)
        ranked_ids = [identifier for ((identifier, _), _) in ranked]
        remaining_ids = [identifier for identifier, _ in candidates if identifier not in set(ranked_ids)]
        return RerankOutcome(
            order=tuple(ranked_ids + remaining_ids),
            scores={identifier: float(score) for ((identifier, _), score) in ranked},
            status="applied",
        )
