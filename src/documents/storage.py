"""Private, validated file storage for user-uploaded PDFs and Markdown."""

from __future__ import annotations

import codecs
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from fastapi import UploadFile

from src.config import DocumentConfig


class DocumentStorageError(RuntimeError):
    """Base error for upload validation and private storage operations."""


class UnsupportedDocumentTypeError(DocumentStorageError):
    """The filename, MIME type, or file signature is not an accepted document."""


class DocumentTooLargeError(DocumentStorageError):
    """The streamed upload exceeded the configured single-file limit."""


@dataclass(frozen=True)
class StoredUpload:
    source_filename: str
    source_media_type: str
    source_size: int
    source_sha256: str
    source_path: str


class DocumentStorage:
    """Store files only below owner/document/version UUID paths, never display names."""

    _ALLOWED = {
        ".pdf": {"application/pdf"},
        ".md": {"text/markdown", "text/plain"},
        ".markdown": {"text/markdown", "text/plain"},
    }
    _CHUNK_SIZE = 64 * 1024
    _CONVERTED_MARKDOWN: Final[str] = "converted.md"
    _IMAGES_DIRECTORY: Final[str] = "images"

    def __init__(self, config: DocumentConfig):
        self.config = config
        self.root = Path(config.storage_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_filename(filename: str | None) -> str:
        candidate = Path(filename or "upload").name.strip()
        if not candidate or candidate in {".", ".."}:
            raise UnsupportedDocumentTypeError("a filename with a supported extension is required")
        return candidate

    def _kind(self, filename: str, media_type: str | None) -> tuple[str, str]:
        extension = Path(filename).suffix.lower()
        allowed_types = self._ALLOWED.get(extension)
        normalized_media_type = (media_type or "").split(";", 1)[0].strip().lower()
        if allowed_types is None or normalized_media_type not in allowed_types:
            raise UnsupportedDocumentTypeError("filename extension and declared MIME type must match")
        return extension, normalized_media_type

    def _version_directory(self, owner_id: str, document_id: str, version_id: str) -> Path:
        if not all(value and value.replace("_", "").replace("-", "").isalnum() for value in (owner_id, document_id, version_id)):
            raise DocumentStorageError("invalid private storage identifier")
        directory = (self.root / owner_id / document_id / version_id).resolve()
        if not directory.is_relative_to(self.root):
            raise DocumentStorageError("unsafe private storage path")
        return directory

    @staticmethod
    def _safe_artifact_name(value: str) -> str:
        candidate = Path(value).name
        if not candidate or candidate in {".", ".."} or candidate != value:
            raise DocumentStorageError("invalid derived artifact name")
        return candidate

    def resolve_version_file(
        self,
        relative_path: str,
        *,
        owner_id: str,
        document_id: str,
        version_id: str,
    ) -> Path:
        """Resolve an internal database path without allowing it to escape its version directory."""

        version_directory = self._version_directory(owner_id, document_id, version_id)
        candidate = (self.root / Path(relative_path)).resolve()
        if not candidate.is_relative_to(version_directory) or not candidate.is_file():
            raise DocumentStorageError("private document file is unavailable")
        return candidate

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f".{uuid4().hex}.writing")
        try:
            with temporary_path.open("xb") as handle:
                handle.write(content)
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def write_converted_markdown(
        self,
        markdown: str,
        *,
        owner_id: str,
        document_id: str,
        version_id: str,
    ) -> str:
        """Persist normalized Markdown beside the source file using an atomic replacement."""

        version_directory = self._version_directory(owner_id, document_id, version_id)
        destination = version_directory / self._CONVERTED_MARKDOWN
        self._atomic_write(destination, markdown.encode("utf-8"))
        return destination.relative_to(self.root).as_posix()

    def write_extracted_image(
        self,
        image_id: str,
        content: bytes,
        *,
        suffix: str,
        owner_id: str,
        document_id: str,
        version_id: str,
    ) -> str:
        """Write a converter-produced image under a generated, non-user-controlled name."""

        safe_id = self._safe_artifact_name(image_id)
        normalized_suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
        if normalized_suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            normalized_suffix = ".png"
        version_directory = self._version_directory(owner_id, document_id, version_id)
        destination = version_directory / self._IMAGES_DIRECTORY / f"{safe_id}{normalized_suffix}"
        self._atomic_write(destination, content)
        return destination.relative_to(self.root).as_posix()

    def clear_derived_artifacts(self, *, owner_id: str, document_id: str, version_id: str) -> None:
        """Remove only retryable conversion outputs while preserving the validated source upload."""

        version_directory = self._version_directory(owner_id, document_id, version_id)
        (version_directory / self._CONVERTED_MARKDOWN).unlink(missing_ok=True)
        images_directory = version_directory / self._IMAGES_DIRECTORY
        if images_directory.exists():
            shutil.rmtree(images_directory)

    @staticmethod
    def _validate_markdown_utf8(path: Path) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(DocumentStorage._CHUNK_SIZE):
                    decoder.decode(chunk)
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise UnsupportedDocumentTypeError("Markdown uploads must be valid UTF-8") from error

    @staticmethod
    def _validate_pdf_signature(path: Path) -> None:
        with path.open("rb") as handle:
            signature = handle.read(5)
        if signature != b"%PDF-":
            raise UnsupportedDocumentTypeError("PDF uploads must have a valid PDF file signature")

    async def store_upload(
        self,
        upload: UploadFile,
        *,
        owner_id: str,
        document_id: str,
        version_id: str,
    ) -> StoredUpload:
        """Stream, validate, and atomically place an upload in its private version directory."""

        filename = self._safe_filename(upload.filename)
        extension, media_type = self._kind(filename, upload.content_type)
        version_directory = self._version_directory(owner_id, document_id, version_id)
        version_directory.mkdir(parents=True, exist_ok=False)
        temporary_path = version_directory / f".{uuid4().hex}.uploading"
        final_path = version_directory / f"source{extension}"
        digest = hashlib.sha256()
        total_bytes = 0
        try:
            with temporary_path.open("xb") as handle:
                while chunk := await upload.read(self._CHUNK_SIZE):
                    total_bytes += len(chunk)
                    if total_bytes > self.config.max_file_bytes:
                        raise DocumentTooLargeError("uploaded file exceeds the configured size limit")
                    digest.update(chunk)
                    handle.write(chunk)
            if extension == ".pdf":
                self._validate_pdf_signature(temporary_path)
            else:
                self._validate_markdown_utf8(temporary_path)
            os.replace(temporary_path, final_path)
            return StoredUpload(
                source_filename=filename,
                source_media_type=media_type,
                source_size=total_bytes,
                source_sha256=digest.hexdigest(),
                source_path=final_path.relative_to(self.root).as_posix(),
            )
        except Exception:
            temporary_path.unlink(missing_ok=True)
            self.remove_version(owner_id=owner_id, document_id=document_id, version_id=version_id)
            raise
        finally:
            await upload.close()

    def remove_version(self, *, owner_id: str, document_id: str, version_id: str) -> None:
        """Safely remove one UUID-derived private version directory."""

        directory = self._version_directory(owner_id, document_id, version_id)
        if directory.exists():
            shutil.rmtree(directory)
