"""Concurrent first-use of lazy model singletons must build exactly one instance."""

import sys
import threading
import time
from types import SimpleNamespace

from src.config import EmbeddingsConfig, RerankerConfig
from src.documents import index as index_module
from src.documents import reranker as reranker_module
from src.documents.index import DocumentIndexService
from src.documents.reranker import FlagEmbeddingReranker


def _race(thread_count, target):
    barrier = threading.Barrier(thread_count)
    results = []
    errors = []

    def worker():
        barrier.wait()
        try:
            results.append(target())
        except Exception as error:  # pragma: no cover - only on regression
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    return results


def test_index_embeddings_load_once_under_concurrency(monkeypatch):
    calls = []

    def slow_loader(config):
        calls.append(1)
        time.sleep(0.05)
        return SimpleNamespace(source="fake-embeddings", serial=len(calls))

    monkeypatch.setattr(index_module, "create_embeddings", slow_loader)
    service = DocumentIndexService(None, None, EmbeddingsConfig())

    results = _race(8, lambda: service.embeddings)

    assert len(calls) == 1
    assert all(result is results[0] for result in results)


def test_index_vector_store_loads_once_under_concurrency(monkeypatch):
    calls = []

    class SlowStore:
        def __init__(self, document_config):
            calls.append(1)
            time.sleep(0.05)

    monkeypatch.setattr(index_module, "ChromaDocumentVectorStore", SlowStore)
    service = DocumentIndexService(None, None, EmbeddingsConfig())

    results = _race(8, lambda: service.vector_store)

    assert len(calls) == 1
    assert all(result is results[0] for result in results)


def test_reranker_model_loads_once_under_concurrency(monkeypatch):
    calls = []

    class SlowFlagReranker:
        def __init__(self, model, use_fp16):
            calls.append({"model": model, "use_fp16": use_fp16})
            time.sleep(0.05)

    monkeypatch.setitem(sys.modules, "FlagEmbedding", SimpleNamespace(FlagReranker=SlowFlagReranker))
    reranker = FlagEmbeddingReranker(RerankerConfig(device="cuda:1"))

    results = _race(8, lambda: reranker.model)

    assert len(calls) == 1
    assert calls[0] == {"model": "BAAI/bge-reranker-v2-m3", "use_fp16": True}
    assert all(result is results[0] for result in results)


def test_reranker_scores_survive_concurrent_first_use(monkeypatch):
    """The full score() path stays functional when the first call races the lazy load."""

    class CountingReranker:
        def __init__(self, model, use_fp16):
            time.sleep(0.02)

        def compute_score(self, pairs, **kwargs):
            time.sleep(0.02)
            return [0.5 for _ in pairs]

    monkeypatch.setitem(sys.modules, "FlagEmbedding", SimpleNamespace(FlagReranker=CountingReranker))
    reranker = reranker_module.FlagEmbeddingReranker(RerankerConfig(device="cpu"))

    scores = _race(6, lambda: reranker.score("q", ["d1", "d2"]))

    assert scores == [[0.5, 0.5]] * 6
