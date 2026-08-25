"""Run the P2.7 controlled acceptance flow against one local research-paper PDF.

The script intentionally uses deterministic embedding/reranker/VLM adapters. It verifies the
real PDF fallback parser, chunking, SQLite/FTS5, Chroma lifecycle, authorization scope, and
privacy-safe diagnostics without downloading model weights or printing private document text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import DocumentConfig, DocumentRetrievalConfig, DocumentVLMConfig, EmbeddingsConfig, RerankerConfig
from src.documents.conversion import DocumentConversionError, DocumentConversionService
from src.documents.index import ChromaDocumentVectorStore, DocumentIndexService
from src.documents.jobs import DocumentWorker
from src.documents.pipeline import DocumentIngestionPipeline
from src.documents.repository import DocumentRepository
from src.documents.retrieval import DocumentRetrievalService
from src.documents.reranker import DocumentRerankerService
from src.documents.storage import DocumentStorage, StoredUpload
from src.events import redact_payload
from src.repository import SQLiteRepository
from src.state import DocumentScope, new_id
from src.tools.documents import create_document_search_tool


class _ForcedDoclingFailure:
    """Select the real MarkItDown fallback without starting Docling model downloads."""

    def convert(self, _source_path: Path):
        raise DocumentConversionError("controlled acceptance: Docling weights unavailable")


class _DeterministicEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [float(digest[0] + 1), float(digest[1] + 1), float(digest[2] + 1)]


class _DeterministicReranker:
    def score(self, _query: str, documents: list[str]) -> list[float]:
        return [float(index + 1) for index, _document in enumerate(documents)]


@dataclass(frozen=True)
class _AcceptanceResult:
    source_label: str
    converter: str
    markdown_chars: int
    parent_count: int
    child_count: int
    image_count: int
    vision_status: str
    initial_retrieval_count: int
    normal_reranker_status: str
    degraded_reranker_status: str
    deleted_retrieval_count: int
    restored_retrieval_count: int
    purged: bool
    cross_owner_retrieval_count: int
    diagnostics_redacted: bool


def _copy_source(storage: DocumentStorage, source: Path, *, owner_id: str, document_id: str, version_id: str) -> StoredUpload:
    version_directory = storage._version_directory(owner_id, document_id, version_id)
    version_directory.mkdir(parents=True, exist_ok=False)
    destination = version_directory / f"source{source.suffix.lower()}"
    shutil.copyfile(source, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return StoredUpload(
        source_filename=source.name,
        source_media_type="application/pdf",
        source_size=destination.stat().st_size,
        source_sha256=digest,
        source_path=str(destination.relative_to(storage.root)).replace("\\", "/"),
    )


def _query_from_markdown(markdown: str) -> str:
    terms = [term for term in re.findall(r"[A-Za-z][A-Za-z-]{4,}", markdown) if term.lower() not in {"https", "creativecommons"}]
    if not terms:
        raise RuntimeError("real PDF fallback produced no searchable alphabetic term")
    return terms[0]


def _count_rows(database_path: Path, query: str, version_id: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        return int(connection.execute(query, (version_id,)).fetchone()[0])
    finally:
        connection.close()


def _run(source: Path) -> _AcceptanceResult:
    with tempfile.TemporaryDirectory(prefix="p2_7_acceptance_") as temporary:
        root = Path(temporary)
        config = DocumentConfig(
            storage_root=str(root / "private"),
            chroma_persist_dir=str(root / "chroma"),
            parent_target_tokens=500,
            child_overlap_ratio=0.12,
        )
        storage = DocumentStorage(config)
        core = SQLiteRepository(root / "research.db")
        owner_a = core.create_user("acceptance-a", "password-hash")["user_id"]
        owner_b = core.create_user("acceptance-b", "password-hash")["user_id"]
        repository = DocumentRepository(core.database_path, config)
        vector_store = None
        try:
            document_id, version_id = new_id("doc"), new_id("ver")
            upload = _copy_source(storage, source, owner_id=owner_a, document_id=document_id, version_id=version_id)
            repository.create_document(upload, owner_id=owner_a, document_id=document_id, version_id=version_id)

            conversion = DocumentConversionService(config, docling_converter=_ForcedDoclingFailure())
            vector_store = ChromaDocumentVectorStore(config)
            index = DocumentIndexService(
                repository,
                config,
                EmbeddingsConfig(provider="huggingface", model="acceptance-deterministic"),
                embeddings=_DeterministicEmbeddings(),
                vector_store=vector_store,
            )
            pipeline = DocumentIngestionPipeline(
                repository,
                storage,
                config,
                DocumentVLMConfig(),
                conversion_service=conversion,
                index_service=index,
            )
            worker = DocumentWorker(repository, pipeline, worker_id="p2-7-acceptance")
            outcome = worker.run_once()
            if outcome.status != "succeeded":
                raise RuntimeError(f"real paper ingestion failed: {outcome.error_code}")

            detail = repository.get_document(document_id, owner_id=owner_a)
            current = detail["current_version"]
            parent_count = _count_rows(
                repository.database_path,
                "SELECT COUNT(*) FROM document_parents WHERE version_id = ?",
                version_id,
            )
            child_count = _count_rows(
                repository.database_path,
                """SELECT COUNT(*) FROM document_chunks AS chunks
                   JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
                   WHERE parents.version_id = ?""",
                version_id,
            )
            image_count = _count_rows(
                repository.database_path,
                "SELECT COUNT(*) FROM document_images WHERE version_id = ?",
                version_id,
            )
            # The converted text is read only to derive a stable smoke query. It is never printed,
            # persisted in the report, or sent to an external service by this script.
            converted_markdown = storage.resolve_version_file(
                detail["versions"][0]["markdown_path"],
                owner_id=owner_a,
                document_id=document_id,
                version_id=version_id,
            ).read_text(encoding="utf-8")
            query = _query_from_markdown(converted_markdown)

            reranker = DocumentRerankerService(RerankerConfig(top_k=8), reranker=_DeterministicReranker())
            retrieval = DocumentRetrievalService(
                repository,
                index,
                DocumentRetrievalConfig(vector_top_k=10, bm25_top_k=10, parent_candidate_k=8, neighbor_window=1),
                reranker,
            )
            initial = retrieval.search(query, owner_id=owner_a, version_ids=[version_id])
            degraded = DocumentRetrievalService(
                repository,
                index,
                DocumentRetrievalConfig(vector_top_k=10, bm25_top_k=10, parent_candidate_k=8),
                DocumentRerankerService(RerankerConfig(top_k=8), reranker=_FailingReranker()),
            ).search(query, owner_id=owner_a, version_ids=[version_id])
            cross_owner = retrieval.search(query, owner_id=owner_b, version_ids=[version_id])

            scope = DocumentScope(selection_mode="explicit", version_ids=[version_id])
            source_payload = json.loads(create_document_search_tool(retrieval, owner_id=owner_a, document_scope=scope).invoke({"query": query}))
            if not source_payload["results"] or "第 " not in source_payload["results"][0]["locator"] and "章节" not in source_payload["results"][0]["locator"]:
                raise RuntimeError("private source did not retain a usable locator")
            source_json = json.dumps(source_payload, ensure_ascii=False)
            if any(value in source_json for value in (owner_a, document_id, version_id, upload.source_path)):
                raise RuntimeError("private source payload leaked an internal identity or storage path")

            repository.delete_document(document_id, owner_id=owner_a)
            index.sync_document_state(document_id, owner_id=owner_a)
            deleted = retrieval.search(query, owner_id=owner_a, version_ids=[version_id])
            repository.restore_document(document_id, owner_id=owner_a)
            index.sync_document_state(document_id, owner_id=owner_a)
            restored = retrieval.search(query, owner_id=owner_a, version_ids=[version_id])
            repository.delete_document(document_id, owner_id=owner_a)
            purged = index.purge_expired_documents(
                storage,
                now=datetime.now(timezone.utc) + timedelta(days=31),
            )
            if purged != 1:
                raise RuntimeError("expired document cleanup did not remove one document")
            if vector_store._collection.count() != 0:
                raise RuntimeError("expired document cleanup left Chroma records")
            try:
                repository.get_document(document_id, owner_id=owner_a)
            except Exception:
                pass
            else:
                raise RuntimeError("expired document cleanup left SQLite document metadata")

            diagnostics = redact_payload(
                {
                    "evidence_excerpt": "private paper text",
                    "source_path": "private/owner/document/source.pdf",
                    "authorization": "Bearer private-token",
                    "status": "succeeded",
                }
            )
            diagnostics_redacted = diagnostics["evidence_excerpt"] == "[REDACTED_CONTENT]" and diagnostics["source_path"] == "[REDACTED_CONTENT]" and diagnostics["authorization"] == "[REDACTED]"
            return _AcceptanceResult(
                source_label=source.name,
                converter="markitdown_pdf_fallback",
                markdown_chars=len(converted_markdown),
                parent_count=parent_count,
                child_count=child_count,
                image_count=image_count,
                vision_status=str(current["vision_status"]),
                initial_retrieval_count=len(initial.parents),
                normal_reranker_status=initial.reranker_status,
                degraded_reranker_status=degraded.reranker_status,
                deleted_retrieval_count=len(deleted.parents),
                restored_retrieval_count=len(restored.parents),
                purged=bool(purged),
                cross_owner_retrieval_count=len(cross_owner.parents),
                diagnostics_redacted=diagnostics_redacted,
            )
        finally:
            if vector_store is not None:
                vector_store.close()
            core.close()


class _FailingReranker:
    def score(self, _query: str, _documents: list[str]) -> list[float]:
        raise RuntimeError("controlled acceptance reranker failure")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="path to one local native-text research paper PDF")
    args = parser.parse_args()
    if args.pdf.suffix.lower() != ".pdf" or not args.pdf.is_file():
        parser.error("pdf must be an existing .pdf file")
    result = _run(args.pdf.resolve())
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
