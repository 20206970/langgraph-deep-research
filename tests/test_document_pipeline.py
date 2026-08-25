import asyncio
import sqlite3
from io import BytesIO

from fastapi import UploadFile
from starlette.datastructures import Headers

from src.config import DocumentConfig, DocumentVLMConfig
from src.documents.conversion import BlockLocator, ConvertedDocument, ExtractedImage
from src.documents.jobs import DocumentWorker
from src.documents.pipeline import DocumentIngestionPipeline
from src.documents.repository import DocumentRepository
from src.documents.storage import DocumentStorage
from src.documents.vision import VisionDescription, VisionProviderError
from src.repository import SQLiteRepository
from src.state import new_id


def _upload(filename: str, content: bytes, media_type: str) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=filename, headers=Headers({"content-type": media_type}))


class _Converted:
    def __init__(self, converted: ConvertedDocument):
        self.converted = converted

    def convert(self, _source, *, media_type: str):
        assert media_type in {"application/pdf", "text/markdown"}
        return self.converted


class _Vision:
    def describe(self, _path, *, caption=None):
        return VisionDescription(
            description="The line rises across the observed epochs.",
            caption_completion=caption,
            entities=["accuracy"],
            trends=["increasing"],
            uncertainty="Values are approximate.",
        )


class _FailingVision:
    def describe(self, _path, *, caption=None):
        raise VisionProviderError("remote detail must not persist")


class _IndexRecorder:
    def __init__(self):
        self.indexed: list[str] = []
        self.synced: list[tuple[str, str]] = []

    def index_job(self, job_id):
        self.indexed.append(job_id)

    def sync_document_state(self, document_id, *, owner_id):
        self.synced.append((document_id, owner_id))


def _context(tmp_path, *, converted: ConvertedDocument, vlm_config: DocumentVLMConfig, provider=None):
    document_config = DocumentConfig(storage_root=str(tmp_path / "private"), stage_timeout_seconds=10)
    core = SQLiteRepository(tmp_path / "research.db")
    owner = core.create_user("pipeline-user", "password-hash")
    repository = DocumentRepository(core.database_path, document_config)
    storage = DocumentStorage(document_config)
    document_id = new_id("doc")
    version_id = new_id("ver")
    stored = asyncio.run(
        storage.store_upload(
            _upload("paper.md", b"# Input", "text/markdown"),
            owner_id=owner["user_id"],
            document_id=document_id,
            version_id=version_id,
        )
    )
    repository.create_document(stored, owner_id=owner["user_id"], document_id=document_id, version_id=version_id)
    pipeline = DocumentIngestionPipeline(
        repository,
        storage,
        document_config,
        vlm_config,
        conversion_service=_Converted(converted),
        vision_provider=provider,
    )
    return core, repository, storage, owner["user_id"], document_id, pipeline


def _artifact_counts(repository: DocumentRepository, version_id: str):
    connection = sqlite3.connect(repository.database_path)
    try:
        parents = connection.execute("SELECT COUNT(*) FROM document_parents WHERE version_id = ?", (version_id,)).fetchone()[0]
        chunks = connection.execute(
            """
            SELECT COUNT(*) FROM document_chunks AS chunks
            JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
            WHERE parents.version_id = ?
            """,
            (version_id,),
        ).fetchone()[0]
        images = connection.execute("SELECT COUNT(*) FROM document_images WHERE version_id = ?", (version_id,)).fetchone()[0]
        return parents, chunks, images
    finally:
        connection.close()


def test_pipeline_persists_markdown_parents_and_chunks_before_worker_finalizes(tmp_path):
    converted = ConvertedDocument(
        markdown="# Paper\n\n<!-- page: 1 -->\n## Methods\n\nThe method description is available for retrieval.",
        title="paper",
        converter_fingerprint="fake-converter",
    )
    core, repository, storage, owner_id, document_id, pipeline = _context(
        tmp_path, converted=converted, vlm_config=DocumentVLMConfig()
    )
    try:
        result = DocumentWorker(repository, pipeline, worker_id="pipeline-worker").run_once()
        detail = repository.get_document(document_id, owner_id=owner_id)
        version = detail["current_version"]

        assert result.status == "succeeded"
        assert version["status"] == "ready"
        assert version["vision_status"] == "not_configured"
        assert version["markdown_path"].endswith("converted.md")
        assert storage.resolve_version_file(
            version["markdown_path"],
            owner_id=owner_id,
            document_id=document_id,
            version_id=version["version_id"],
        ).read_text(encoding="utf-8").startswith("# Paper")
        parents, chunks, images = _artifact_counts(repository, version["version_id"])
        assert parents == 1
        assert chunks >= 1
        assert images == 0
    finally:
        core.close()


def test_pipeline_keeps_text_ready_when_one_visual_enrichment_fails(tmp_path):
    converted = ConvertedDocument(
        markdown="# Paper\n\n<!-- page: 2 -->\n## Results\n\nThe paper discusses the result.",
        title="paper",
        converter_fingerprint="fake-converter",
        images=(
            ExtractedImage(
                image_id="img_visual",
                content=b"png-data",
                locator=BlockLocator(heading_path=("Paper", "Results"), page_start=2, page_end=2),
                caption="Figure 1",
            ),
        ),
    )
    config = DocumentVLMConfig(provider="openai-compatible", api_key="vision-key", model="vision-model")
    core, repository, _storage, owner_id, document_id, pipeline = _context(
        tmp_path, converted=converted, vlm_config=config, provider=_FailingVision()
    )
    try:
        result = DocumentWorker(repository, pipeline, worker_id="pipeline-worker").run_once()
        detail = repository.get_document(document_id, owner_id=owner_id)
        version = detail["current_version"]

        assert result.status == "succeeded"
        assert version["status"] == "ready"
        assert version["vision_status"] == "partial"
        parents, chunks, images = _artifact_counts(repository, version["version_id"])
        assert parents == 1
        assert chunks >= 1
        assert images == 1
    finally:
        core.close()


def test_pipeline_adds_non_source_visual_chunk_when_vlm_succeeds(tmp_path):
    converted = ConvertedDocument(
        markdown="# Paper\n\n<!-- page: 2 -->\n## Results\n\nThe paper discusses the result.",
        title="paper",
        converter_fingerprint="fake-converter",
        images=(
            ExtractedImage(
                image_id="img_visual",
                content=b"png-data",
                locator=BlockLocator(heading_path=("Paper", "Results"), page_start=2, page_end=2),
            ),
        ),
    )
    config = DocumentVLMConfig(provider="openai-compatible", api_key="vision-key", model="vision-model")
    core, repository, _storage, owner_id, document_id, pipeline = _context(
        tmp_path, converted=converted, vlm_config=config, provider=_Vision()
    )
    try:
        assert DocumentWorker(repository, pipeline, worker_id="pipeline-worker").run_once().status == "succeeded"
        version = repository.get_document(document_id, owner_id=owner_id)["current_version"]
        connection = sqlite3.connect(repository.database_path)
        try:
            visual_text = connection.execute(
                """
                SELECT chunks.text FROM document_chunks AS chunks
                JOIN document_parents AS parents ON parents.parent_id = chunks.parent_id
                WHERE parents.version_id = ? AND chunks.kind = 'vision'
                """,
                (version["version_id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        assert version["vision_status"] == "succeeded"
        assert visual_text.startswith("Visual enhancement (non-source):")
    finally:
        core.close()


def test_pipeline_indexes_before_ready_promotion_and_syncs_vector_lifecycle_afterward(tmp_path):
    converted = ConvertedDocument(
        markdown="# Paper\n\n## Methods\n\nIndexed retrieval text.",
        title="paper",
        converter_fingerprint="fake-converter",
    )
    core, repository, storage, owner_id, document_id, _pipeline = _context(
        tmp_path, converted=converted, vlm_config=DocumentVLMConfig()
    )
    recorder = _IndexRecorder()
    pipeline = DocumentIngestionPipeline(
        repository,
        storage,
        DocumentConfig(storage_root=str(tmp_path / "private"), stage_timeout_seconds=10),
        DocumentVLMConfig(),
        conversion_service=_Converted(converted),
        index_service=recorder,
    )
    try:
        result = DocumentWorker(repository, pipeline, worker_id="pipeline-worker").run_once()
        job_id = repository.get_document(document_id, owner_id=owner_id)["jobs"][0]["job_id"]

        assert result.status == "succeeded"
        assert recorder.indexed == [job_id]
        assert recorder.synced == [(document_id, owner_id)]
    finally:
        core.close()
