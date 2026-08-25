"""P2.3 document ingestion handler: conversion, optional vision, and parent/child chunking."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Callable, TypeVar

from src.config import Config, DocumentConfig, DocumentVLMConfig

from .chunking import PreparedImage, chunk_document
from .conversion import ConvertedDocument, DocumentConversionError, DocumentConversionService, ExtractedImage
from .index import DocumentIndexError, DocumentIndexService
from .jobs import IngestionProcessingError
from .models import DocumentImage, IngestionStage, VisionStatus
from .repository import DocumentRepository
from .storage import DocumentStorage
from .vision import VisionProvider, VisionProviderError, build_vision_provider, enrich_image


_Result = TypeVar("_Result")


class DocumentIngestionPipeline:
    """A synchronous handler suitable for ``DocumentWorker`` and deterministic fake-adapter tests."""

    def __init__(
        self,
        repository: DocumentRepository,
        storage: DocumentStorage,
        document_config: DocumentConfig,
        vision_config: DocumentVLMConfig,
        *,
        conversion_service: DocumentConversionService | None = None,
        vision_provider: VisionProvider | None = None,
        index_service: DocumentIndexService | None = None,
    ):
        self.repository = repository
        self.storage = storage
        self.document_config = document_config
        self.vision_config = vision_config
        self.conversion_service = conversion_service or DocumentConversionService(document_config)
        self.index_service = index_service
        self.vision_provider_error: VisionProviderError | None = None
        if vision_provider is not None:
            self.vision_provider = vision_provider
        else:
            try:
                self.vision_provider = build_vision_provider(vision_config)
            except VisionProviderError as error:
                self.vision_provider = None
                self.vision_provider_error = error

    def _within_stage_timeout(self, callback: Callable[[], _Result], *, error_code: str, message: str) -> _Result:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="document-stage")
        future = executor.submit(callback)
        try:
            result = future.result(timeout=self.document_config.stage_timeout_seconds)
        except TimeoutError as error:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise IngestionProcessingError(error_code, message) from error
        except Exception:
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        executor.shutdown(wait=True, cancel_futures=True)
        return result

    @staticmethod
    def _vision_status(*, images: list[DocumentImage], visual_capability_limited: bool, configured: bool) -> VisionStatus:
        if not configured:
            return VisionStatus.NOT_CONFIGURED
        if visual_capability_limited or any(image.vision_status != VisionStatus.SUCCEEDED for image in images):
            return VisionStatus.PARTIAL
        return VisionStatus.SUCCEEDED

    def _materialize_images(self, converted: ConvertedDocument, job: dict[str, object]) -> list[PreparedImage]:
        prepared: list[PreparedImage] = []
        for extracted in converted.images:
            relative_path = self.storage.write_extracted_image(
                extracted.image_id,
                extracted.content,
                suffix=extracted.suffix,
                owner_id=str(job["owner_id"]),
                document_id=str(job["document_id"]),
                version_id=str(job["version_id"]),
            )
            prepared.append(
                PreparedImage(
                    image=DocumentImage(
                        image_id=extracted.image_id,
                        version_id=str(job["version_id"]),
                        page=extracted.locator.page_start,
                        path=relative_path,
                        caption=extracted.caption,
                        vision_status=VisionStatus.PENDING,
                    ),
                    locator=extracted.locator,
                    source_image=extracted,
                )
            )
        return prepared

    def _enrich_images(self, prepared: list[PreparedImage], job: dict[str, object]) -> list[PreparedImage]:
        if not prepared:
            return prepared
        self.repository.update_ingestion_stage(str(job["job_id"]), IngestionStage.VISION_ENRICHING)
        if self.vision_provider is None:
            status = VisionStatus.NOT_CONFIGURED if not self.vision_config.is_configured else VisionStatus.FAILED
            metadata = {"error_code": "VLM_NOT_CONFIGURED" if status == VisionStatus.NOT_CONFIGURED else "VLM_PROVIDER_UNAVAILABLE"}
            return [
                PreparedImage(image=item.image.model_copy(update={"vision_status": status, "vision_metadata": metadata}), locator=item.locator)
                for item in prepared
            ]

        enriched: list[PreparedImage] = []
        for item in prepared:
            try:
                image_path = self.storage.resolve_version_file(
                    item.image.path,
                    owner_id=str(job["owner_id"]),
                    document_id=str(job["document_id"]),
                    version_id=str(job["version_id"]),
                )
                result = self._within_stage_timeout(
                    lambda: enrich_image(
                        self.vision_provider,
                        item.source_image
                        or ExtractedImage(
                            image_id=item.image.image_id,
                            content=b"",
                            locator=item.locator,
                            caption=item.image.caption,
                        ),
                        image_path=image_path,
                        config=self.vision_config,
                    ),
                    error_code="VISION_TIMEOUT",
                    message="visual enhancement timed out",
                )
                enriched.append(
                    PreparedImage(
                        image=item.image.model_copy(update={"vision_status": VisionStatus.SUCCEEDED, "vision_metadata": result.metadata}),
                        locator=item.locator,
                        source_image=item.source_image,
                    )
                )
            except (VisionProviderError, IngestionProcessingError, OSError):
                # Never retain the provider exception: it could include a remote endpoint detail
                # or a user-provided figure caption. The per-image code is sufficient for retry diagnostics.
                enriched.append(
                    PreparedImage(
                        image=item.image.model_copy(
                            update={"vision_status": VisionStatus.FAILED, "vision_metadata": {"error_code": "VISION_FAILED"}}
                        ),
                        locator=item.locator,
                        source_image=item.source_image,
                    )
                )
        return enriched

    def __call__(self, job: dict[str, object]) -> None:
        owner_id = str(job["owner_id"])
        document_id = str(job["document_id"])
        version_id = str(job["version_id"])
        job_id = str(job["job_id"])
        try:
            self.repository.update_ingestion_stage(job_id, IngestionStage.CONVERTING)
            source_path = self.storage.resolve_version_file(
                str(job["source_path"]), owner_id=owner_id, document_id=document_id, version_id=version_id
            )
            self.storage.clear_derived_artifacts(owner_id=owner_id, document_id=document_id, version_id=version_id)
            converted = self._within_stage_timeout(
                lambda: self.conversion_service.convert(source_path, media_type=str(job["source_media_type"])),
                error_code="CONVERSION_TIMEOUT",
                message="document conversion timed out",
            )
        except IngestionProcessingError:
            raise
        except (DocumentConversionError, OSError) as error:
            raise IngestionProcessingError("CONVERSION_FAILED", "document conversion failed") from error

        markdown_path = self.storage.write_converted_markdown(
            converted.markdown, owner_id=owner_id, document_id=document_id, version_id=version_id
        )
        self.repository.record_conversion(
            job_id, markdown_path=markdown_path, converter_fingerprint=converted.converter_fingerprint
        )
        prepared = self._materialize_images(converted, job)
        prepared = self._enrich_images(prepared, job)
        configured = self.vision_config.is_configured
        vision_status = self._vision_status(
            images=[item.image for item in prepared],
            visual_capability_limited=converted.visual_capability_limited,
            configured=configured,
        )
        try:
            self.repository.update_ingestion_stage(job_id, IngestionStage.CHUNKING)
            result = self._within_stage_timeout(
                lambda: chunk_document(
                    converted,
                    version_id=version_id,
                    config=self.document_config,
                    images=prepared,
                ),
                error_code="CHUNKING_TIMEOUT",
                message="document chunking timed out",
            )
            self.repository.replace_ingestion_artifacts(
                job_id,
                parents=result.parents,
                chunks=result.chunks,
                images=result.images,
                vision_status=vision_status,
            )
            # The index remains a visible stage before DocumentWorker atomically promotes the version.
            self.repository.update_ingestion_stage(job_id, IngestionStage.INDEXING)
            if self.index_service is not None:
                try:
                    self.index_service.index_job(job_id)
                except DocumentIndexError as error:
                    raise IngestionProcessingError("INDEXING_FAILED", "document index creation failed") from error
        except IngestionProcessingError:
            raise
        except Exception as error:
            raise IngestionProcessingError("CHUNKING_FAILED", "document chunking failed") from error

    def on_success(self, job: dict[str, object]) -> None:
        """Refresh Chroma flags after the repository atomically promotes or archives versions."""

        if self.index_service is not None:
            self.index_service.sync_document_state(str(job["document_id"]), owner_id=str(job["owner_id"]))


def build_document_ingestion_pipeline(config: Config) -> DocumentIngestionPipeline:
    """Production factory used by the standalone worker CLI."""

    repository = DocumentRepository(config.storage.sqlite_path, config.documents)
    return DocumentIngestionPipeline(
        repository,
        DocumentStorage(config.documents),
        config.documents,
        config.document_vlm,
        index_service=DocumentIndexService(repository, config.documents, config.embeddings),
    )
