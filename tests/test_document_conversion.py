from pathlib import Path

import pytest

from src.config import DocumentConfig
from src.documents.conversion import ConvertedDocument, DocumentConversionError, DocumentConversionService


class _Adapter:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[Path] = []

    def convert(self, source_path: Path):
        self.calls.append(source_path)
        if self.error:
            raise self.error
        return self.result


def test_markdown_conversion_normalizes_text_without_pdf_adapters(tmp_path):
    source = tmp_path / "paper.md"
    source.write_bytes(b"# Paper\r\n\r\nA table:\r\n| A | B |\r\n| - | - |\r\n")
    pdf_adapter = _Adapter(error=AssertionError("PDF adapter must not run for Markdown"))
    service = DocumentConversionService(DocumentConfig(), docling_converter=pdf_adapter)

    converted = service.convert(source, media_type="text/markdown")

    assert converted.markdown == "# Paper\n\nA table:\n| A | B |\n| - | - |\n"
    assert converted.converter_fingerprint == "markdown:utf8-normalize:v1"
    assert not pdf_adapter.calls


def test_pdf_falls_back_to_markitdown_only_after_docling_failure(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7")
    docling = _Adapter(error=DocumentConversionError("Docling failed"))
    fallback = _Adapter(
        result=ConvertedDocument(
            markdown="# Fallback\n\nText\n",
            title="paper",
            converter_fingerprint="fake-markitdown",
            visual_capability_limited=True,
        )
    )
    service = DocumentConversionService(
        DocumentConfig(markitdown_fallback_enabled=True), docling_converter=docling, markitdown_converter=fallback
    )

    converted = service.convert(source, media_type="application/pdf")

    assert converted.converter_fingerprint == "fake-markitdown"
    assert converted.visual_capability_limited is True
    assert docling.calls == [source]
    assert fallback.calls == [source]


def test_pdf_conversion_does_not_use_fallback_when_disabled(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7")
    docling = _Adapter(error=DocumentConversionError("Docling failed"))
    fallback = _Adapter(error=AssertionError("fallback must be disabled"))
    service = DocumentConversionService(
        DocumentConfig(markitdown_fallback_enabled=False), docling_converter=docling, markitdown_converter=fallback
    )

    with pytest.raises(DocumentConversionError, match="Docling failed"):
        service.convert(source, media_type="application/pdf")
    assert not fallback.calls
