import asyncio
from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from src.config import DocumentConfig
from src.documents.storage import DocumentStorage, DocumentTooLargeError, UnsupportedDocumentTypeError


def _upload(filename: str, content: bytes, media_type: str) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=filename, headers=Headers({"content-type": media_type}))


def _storage(tmp_path, **overrides) -> DocumentStorage:
    return DocumentStorage(DocumentConfig(storage_root=str(tmp_path / "private"), **overrides))


def test_storage_streams_valid_markdown_to_uuid_private_path(tmp_path):
    storage = _storage(tmp_path)
    stored = asyncio.run(
        storage.store_upload(
            _upload("paper.md", b"# Study\n\nPrivate text", "text/markdown"),
            owner_id="user_abc",
            document_id="doc_abc",
            version_id="ver_abc",
        )
    )

    assert stored.source_filename == "paper.md"
    assert stored.source_path == "user_abc/doc_abc/ver_abc/source.md"
    assert (storage.root / stored.source_path).read_bytes() == b"# Study\n\nPrivate text"
    assert "paper.md" not in stored.source_path
    assert len(stored.source_sha256) == 64


@pytest.mark.parametrize(
    ("filename", "content", "media_type"),
    [
        ("paper.pdf", b"not a PDF", "application/pdf"),
        ("paper.md", b"\xff\xfe", "text/markdown"),
        ("paper.txt", b"text", "text/plain"),
        ("paper.md", b"text", "application/pdf"),
    ],
)
def test_storage_rejects_invalid_type_signatures_and_encoding(tmp_path, filename, content, media_type):
    storage = _storage(tmp_path)
    with pytest.raises(UnsupportedDocumentTypeError):
        asyncio.run(
            storage.store_upload(
                _upload(filename, content, media_type),
                owner_id="user_abc",
                document_id="doc_abc",
                version_id="ver_abc",
            )
        )
    assert not (storage.root / "user_abc" / "doc_abc" / "ver_abc").exists()


def test_storage_enforces_streamed_size_limit_and_cleans_partial_upload(tmp_path):
    storage = _storage(tmp_path, max_file_bytes=4)
    with pytest.raises(DocumentTooLargeError):
        asyncio.run(
            storage.store_upload(
                _upload("paper.md", b"12345", "text/markdown"),
                owner_id="user_abc",
                document_id="doc_abc",
                version_id="ver_abc",
            )
        )
    assert not (storage.root / "user_abc" / "doc_abc" / "ver_abc").exists()


def test_storage_never_uses_display_filename_for_private_path(tmp_path):
    storage = _storage(tmp_path)
    stored = asyncio.run(
        storage.store_upload(
            _upload("../../escape.md", b"# safe", "text/markdown"),
            owner_id="user_abc",
            document_id="doc_abc",
            version_id="ver_abc",
        )
    )
    assert stored.source_filename == "escape.md"
    assert (storage.root / stored.source_path).is_file()
    assert not (tmp_path / "escape.md").exists()
