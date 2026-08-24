"""Versioned research artifacts and LangGraph state reducers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Optional, TypedDict
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


SCHEMA_VERSION = 1


def utc_now() -> str:
    """Return an ISO-8601 timestamp suitable for JSON state."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Create a stable-prefix identifier for persisted research artifacts."""
    return f"{prefix}_{uuid4().hex}"


class RunStatus(str, Enum):
    PLANNED = "planned"
    CONFIRMED = "confirmed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    PLANNED = "planned"
    CONFIRMED = "confirmed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ParseStatus(str, Enum):
    VALID = "valid"
    REPAIRED = "repaired"
    REJECTED = "rejected"


class TaskItem(BaseModel):
    """A normalized research task with a stable identity."""

    task_id: str = Field(default_factory=lambda: new_id("task"))
    id: int = Field(default=0, ge=0, description="Legacy display order")
    title: str = Field(..., min_length=1, max_length=200)
    intent: str = Field(..., min_length=1, max_length=1_000)
    query: str = Field(..., min_length=1, max_length=500)
    status: TaskStatus = TaskStatus.PLANNED
    query_history: list[str] = Field(default_factory=list)

    @field_validator("title", "intent", "query")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field cannot be blank")
        return value

    @model_validator(mode="after")
    def ensure_query_history_contains_current_query(self) -> "TaskItem":
        if not self.query_history:
            self.query_history = [self.query]
        return self


class TaskPlan(BaseModel):
    """A validated plan produced by Planner before any search is executed."""

    schema_version: int = SCHEMA_VERSION
    plan_id: str = Field(default_factory=lambda: new_id("plan"))
    plan_version: int = Field(default=1, ge=1)
    topic: str = Field(..., min_length=1, max_length=1_000)
    tasks: list[TaskItem] = Field(default_factory=list, max_length=7)
    parse_status: ParseStatus = ParseStatus.VALID
    error_code: Optional[str] = Field(default=None, max_length=100)
    error_message: Optional[str] = Field(default=None, max_length=1_000)

    @field_validator("topic")
    @classmethod
    def strip_topic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("topic cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_task_identifiers_and_queries(self) -> "TaskPlan":
        if self.parse_status != ParseStatus.REJECTED and not self.tasks:
            raise ValueError("a non-rejected plan must contain at least one task")

        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique")

        normalized_queries = [" ".join(task.query.lower().split()) for task in self.tasks]
        if len(normalized_queries) != len(set(normalized_queries)):
            raise ValueError("task queries must be unique")
        return self


class ResearchRun(BaseModel):
    """Metadata that identifies a single graph execution."""

    schema_version: int = SCHEMA_VERSION
    run_id: str = Field(default_factory=lambda: new_id("run"))
    thread_id: str = ""
    plan_id: Optional[str] = None
    plan_version: Optional[int] = Field(default=None, ge=1)
    topic: str = Field(..., min_length=1, max_length=1_000)
    status: RunStatus = RunStatus.PLANNED
    model_versions: dict[str, str] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    output_diagnostics: dict[str, dict[str, Any]] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def default_thread_id(self) -> "ResearchRun":
        if not self.thread_id:
            self.thread_id = self.run_id
        return self


class SourceItem(BaseModel):
    """A source entity. Task ownership is stored separately in TaskSourceRef."""

    source_id: str = Field(default_factory=lambda: new_id("src"))
    source_type: str = Field(default="web", min_length=1, max_length=50)
    provider: str = Field(default="unknown", min_length=1, max_length=50)
    canonical_url: Optional[str] = Field(default=None, max_length=2_000)
    title: Optional[str] = Field(default=None, max_length=500)
    retrieved_at: str = Field(default_factory=utc_now)
    content_hash: Optional[str] = Field(default=None, max_length=128)
    evidence_excerpt: Optional[str] = Field(default=None, max_length=1_500)
    locator: Optional[str] = Field(default=None, max_length=500)


class TaskSourceRef(BaseModel):
    """Associates a source entity with one task and one search attempt."""

    task_id: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1, max_length=500)
    attempt: int = Field(default=1, ge=1)
    associated_at: str = Field(default_factory=utc_now)


class Claim(BaseModel):
    """A reportable conclusion and the source IDs it relies on."""

    claim_id: str = Field(default_factory=lambda: new_id("claim"))
    task_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1, max_length=2_000)
    source_ids: list[str] = Field(default_factory=list)
    evidence_status: str = Field(default="unverified", max_length=50)


class TaskResult(BaseModel):
    """The structured output of one Summarizer execution."""

    task_id: str = Field(..., min_length=1)
    status: TaskStatus = TaskStatus.RUNNING
    attempts: int = Field(default=1, ge=1)
    query_history: list[str] = Field(default_factory=list)
    summary: str = ""
    source_ids: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    error_code: Optional[str] = Field(default=None, max_length=100)
    error_message: Optional[str] = Field(default=None, max_length=1_000)
    latency_ms: Optional[int] = Field(default=None, ge=0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    parse_status: ParseStatus = ParseStatus.VALID

    @model_validator(mode="after")
    def validate_result_references(self) -> "TaskResult":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must be unique")
        for claim in self.claims:
            if claim.task_id != self.task_id:
                raise ValueError("claim task_id must match task result")
        return self


class ReportArtifact(BaseModel):
    """Versioned report metadata while preserving the legacy Markdown field."""

    report_id: str = Field(default_factory=lambda: new_id("report"))
    report_version: int = Field(default=1, ge=1)
    run_id: str = Field(..., min_length=1)
    markdown: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    status: RunStatus = RunStatus.SUCCEEDED
    created_at: str = Field(default_factory=utc_now)


class SearchSource(BaseModel):
    """Legacy source shape retained while P0.2 source normalization is introduced."""

    query: str = Field(..., description="搜索查询")
    url: Optional[str] = Field(default=None, description="来源 URL")
    title: Optional[str] = Field(default=None, description="来源标题")


def merge_task_results(
    left: Optional[dict[str, dict[str, Any]]],
    right: Optional[dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Merge parallel task results and reject conflicting writes for one task."""
    merged = dict(left or {})
    for task_id, result in (right or {}).items():
        if task_id in merged and merged[task_id] != result:
            raise ValueError(f"conflicting task result for {task_id}")
        merged[task_id] = result
    return merged


def merge_sources(
    left: Optional[dict[str, dict[str, Any]]],
    right: Optional[dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Merge sources by ID, preserving the richer evidence record on collision."""
    merged = dict(left or {})
    for source_id, source in (right or {}).items():
        existing = merged.get(source_id)
        if existing is None:
            merged[source_id] = source
            continue
        existing_score = _source_completeness(existing)
        incoming_score = _source_completeness(source)
        if incoming_score > existing_score:
            merged[source_id] = source
    return merged


def _source_completeness(source: dict[str, Any]) -> tuple[int, int]:
    """Prefer records with more metadata, then the longer evidence excerpt."""
    fields = (
        "canonical_url",
        "title",
        "content_hash",
        "evidence_excerpt",
        "locator",
    )
    populated_fields = sum(bool(source.get(field)) for field in fields)
    excerpt_length = len(str(source.get("evidence_excerpt") or ""))
    return populated_fields, excerpt_length


def merge_task_source_refs(
    left: Optional[dict[str, list[dict[str, Any]]]],
    right: Optional[dict[str, list[dict[str, Any]]]],
) -> dict[str, list[dict[str, Any]]]:
    """Merge task/source associations without duplicating one source per task."""
    merged = {task_id: list(refs) for task_id, refs in (left or {}).items()}
    for task_id, refs in (right or {}).items():
        current = merged.setdefault(task_id, [])
        known_source_ids = {ref.get("source_id") for ref in current}
        for ref in refs:
            if ref.get("source_id") not in known_source_ids:
                current.append(ref)
                known_source_ids.add(ref.get("source_id"))
    return merged


def merge_output_diagnostics(
    left: Optional[dict[str, dict[str, Any]]],
    right: Optional[dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Merge hashed model-output metadata without storing raw model text."""
    merged = dict(left or {})
    for key, diagnostic in (right or {}).items():
        if key in merged and merged[key] != diagnostic:
            raise ValueError(f"conflicting output diagnostic for {key}")
        merged[key] = diagnostic
    return merged


class ResearchState(TypedDict, total=False):
    """JSON-compatible LangGraph state with deterministic parallel reducers."""

    topic: str
    run: dict[str, Any]
    plan: dict[str, Any]
    tasks: list[dict[str, Any]]
    task: dict[str, Any]
    task_results: Annotated[dict[str, dict[str, Any]], merge_task_results]
    sources: Annotated[dict[str, dict[str, Any]], merge_sources]
    task_source_refs: Annotated[dict[str, list[dict[str, Any]]], merge_task_source_refs]
    output_diagnostics: Annotated[dict[str, dict[str, Any]], merge_output_diagnostics]
    report: str
    report_artifact: dict[str, Any]
    loop_count: int
    memory_context: str
    session_id: Optional[str]


ResearchStateDict = ResearchState
