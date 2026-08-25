"""PDF and Markdown conversion adapters for the private document ingestion pipeline."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol, Sequence
from uuid import uuid4

from src.config import DocumentConfig


class DocumentConversionError(RuntimeError):
    """A source document could not be converted into safe normalized Markdown."""


@dataclass(frozen=True)
class BlockLocator:
    """Source-location hints retained from a converter without retaining document text in logs."""

    heading_path: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True)
class ExtractedImage:
    """A converter-produced image and the minimal source context required for VLM enrichment."""

    image_id: str
    content: bytes
    suffix: str = ".png"
    locator: BlockLocator = field(default_factory=BlockLocator)
    caption: str | None = None


@dataclass(frozen=True)
class ConvertedDocument:
    """Normalized conversion result consumed by the chunking and vision stages."""

    markdown: str
    title: str
    converter_fingerprint: str
    images: tuple[ExtractedImage, ...] = ()
    visual_capability_limited: bool = False


class ConversionAdapter(Protocol):
    """One input-format converter. Tests inject fakes through this small boundary."""

    def convert(self, source_path: Path) -> ConvertedDocument:
        """Return normalized Markdown or raise ``DocumentConversionError``."""


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"


def _normalize_markdown(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return normalized.strip() + "\n"


def _title_from_source(source_path: Path) -> str:
    return source_path.stem.strip() or "Untitled document"


class MarkdownConverter:
    """Normalize a validated UTF-8 Markdown upload without dereferencing image links."""

    def convert(self, source_path: Path) -> ConvertedDocument:
        try:
            markdown = source_path.read_text(encoding="utf-8")
        except OSError as error:
            raise DocumentConversionError("Markdown source is unavailable") from error
        return ConvertedDocument(
            markdown=_normalize_markdown(markdown),
            title=_title_from_source(source_path),
            converter_fingerprint="markdown:utf8-normalize:v1",
        )


class DoclingConverter:
    """Docling-first PDF conversion with OCR explicitly disabled for native-text papers."""

    def __init__(self, *, ocr_enabled: bool = False):
        self.ocr_enabled = ocr_enabled

    def _build_converter(self):
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as error:
            raise DocumentConversionError("Docling is not installed") from error

        options = PdfPipelineOptions()
        options.do_ocr = self.ocr_enabled
        # Picture generation is necessary for offline VLM enrichment. Older Docling releases
        # may not expose one of these optional toggles, so keep their absence non-fatal.
        for attribute in ("generate_picture_images", "generate_page_images"):
            if hasattr(options, attribute):
                setattr(options, attribute, True)
        return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})

    @staticmethod
    def _caption_text(picture: object) -> str | None:
        captions = getattr(picture, "captions", None) or []
        values = [str(getattr(caption, "text", "")).strip() for caption in captions]
        joined = " ".join(value for value in values if value)
        return joined or None

    @staticmethod
    def _picture_page(picture: object) -> int | None:
        provenance = getattr(picture, "prov", None) or []
        for item in provenance:
            page = getattr(item, "page_no", None)
            if isinstance(page, int) and page > 0:
                return page
        return None

    def _extract_images(self, document: object) -> tuple[tuple[ExtractedImage, ...], bool]:
        """Best-effort extraction across Docling minor versions; conversion itself stays usable."""

        pictures: Sequence[object] = getattr(document, "pictures", None) or []
        extracted: list[ExtractedImage] = []
        complete = True
        for picture in pictures:
            try:
                image = picture.get_image(document)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                extracted.append(
                    ExtractedImage(
                        image_id=f"img_{uuid4().hex}",
                        content=buffer.getvalue(),
                        suffix=".png",
                        locator=BlockLocator(page_start=self._picture_page(picture), page_end=self._picture_page(picture)),
                        caption=self._caption_text(picture),
                    )
                )
            except Exception:
                # Images are an enhancement path. Do not discard a successful text conversion
                # because one visual artifact cannot be materialized.
                complete = False
        return tuple(extracted), complete

    def convert(self, source_path: Path) -> ConvertedDocument:
        try:
            result = self._build_converter().convert(str(source_path))
            document = result.document
            markdown = document.export_to_markdown()
            images, images_complete = self._extract_images(document)
        except DocumentConversionError:
            raise
        except Exception as error:
            raise DocumentConversionError("Docling PDF conversion failed") from error
        return ConvertedDocument(
            markdown=_normalize_markdown(str(markdown)),
            title=_title_from_source(source_path),
            converter_fingerprint=f"docling:{_package_version('docling')}:ocr_{str(self.ocr_enabled).lower()}",
            images=images,
            visual_capability_limited=not images_complete,
        )


class MarkItDownConverter:
    """Text-only fallback when Docling cannot convert a PDF."""

    def convert(self, source_path: Path) -> ConvertedDocument:
        try:
            from markitdown import MarkItDown
        except ImportError as error:
            raise DocumentConversionError("MarkItDown is not installed") from error
        try:
            result = MarkItDown(enable_plugins=False).convert(str(source_path))
            markdown = result.text_content
        except Exception as error:
            raise DocumentConversionError("MarkItDown PDF fallback failed") from error
        return ConvertedDocument(
            markdown=_normalize_markdown(str(markdown)),
            title=_title_from_source(source_path),
            converter_fingerprint=f"markitdown:{_package_version('markitdown')}:text_fallback",
            visual_capability_limited=True,
        )


class DocumentConversionService:
    """Select the safe conversion path; Docling failures alone permit a MarkItDown fallback."""

    def __init__(
        self,
        config: DocumentConfig,
        *,
        markdown_converter: ConversionAdapter | None = None,
        docling_converter: ConversionAdapter | None = None,
        markitdown_converter: ConversionAdapter | None = None,
    ):
        self.config = config
        self.markdown_converter = markdown_converter or MarkdownConverter()
        self.docling_converter = docling_converter or DoclingConverter(ocr_enabled=config.docling_ocr_enabled)
        self.markitdown_converter = markitdown_converter or MarkItDownConverter()

    def convert(self, source_path: Path, *, media_type: str) -> ConvertedDocument:
        if media_type in {"text/markdown", "text/plain"}:
            return self.markdown_converter.convert(source_path)
        if media_type != "application/pdf":
            raise DocumentConversionError("unsupported document media type")
        try:
            return self.docling_converter.convert(source_path)
        except DocumentConversionError as docling_error:
            if not self.config.markitdown_fallback_enabled:
                raise docling_error
            try:
                return self.markitdown_converter.convert(source_path)
            except DocumentConversionError as fallback_error:
                raise DocumentConversionError("PDF conversion failed in Docling and MarkItDown") from fallback_error
