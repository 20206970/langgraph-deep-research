"""State contracts for private documents and their asynchronous ingestion lifecycle."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentVersionStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"
    DELETED = "deleted"


class IngestionJobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestionStage(str, Enum):
    QUEUED = "queued"
    CONVERTING = "converting"
    VISION_ENRICHING = "vision_enriching"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    COMPLETE = "complete"


class VisionStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class DocumentVersionView(BaseModel):
    version_id: str
    version_number: int
    source_filename: str
    source_media_type: str
    source_size: int = Field(ge=0)
    status: DocumentVersionStatus
    is_current: bool
    retrieval_enabled: bool
    vision_status: VisionStatus
    error_code: str | None = None
    error_summary: str | None = None
    created_at: str
    updated_at: str


class IngestionJobView(BaseModel):
    job_id: str
    version_id: str
    status: IngestionJobStatus
    stage: IngestionStage
    attempt: int = Field(ge=0)
    lease_until: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    created_at: str
    updated_at: str


class DocumentView(BaseModel):
    document_id: str
    title: str
    deleted_at: str | None = None
    created_at: str
    updated_at: str
    current_version: DocumentVersionView | None = None


class DocumentDetailView(DocumentView):
    versions: list[DocumentVersionView] = Field(default_factory=list)
    jobs: list[IngestionJobView] = Field(default_factory=list)


class DocumentListResponse(BaseModel):
    items: list[DocumentView] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class StorageUsageView(BaseModel):
    used_bytes: int = Field(ge=0)
    quota_bytes: int = Field(gt=0)
    remaining_bytes: int = Field(ge=0)


class DocumentImage(BaseModel):
    image_id: str
    version_id: str
    parent_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    path: str
    caption: str | None = None
    vision_status: VisionStatus = VisionStatus.PENDING
    vision_metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentParent(BaseModel):
    parent_id: str
    version_id: str
    logical_heading_path: str
    physical_index: int = Field(ge=0)
    text: str
    locator: str | None = None


class DocumentChunk(BaseModel):
    chunk_id: str
    parent_id: str
    kind: str = Field(min_length=1, max_length=32)
    text: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    chroma_id: str | None = None
    fts_rowid: int | None = None
