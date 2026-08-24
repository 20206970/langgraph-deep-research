"""Structured local events and standard Server-Sent Events encoding."""

from __future__ import annotations

import json
import queue
import re
import threading
from enum import Enum
from typing import Any, Iterator

from loguru import logger
from pydantic import BaseModel, Field

from src.state import new_id, utc_now


class EventType(str, Enum):
    PLANNING = "planning"
    PLAN_CONFIRMED = "plan_confirmed"
    TASK_STARTED = "task_started"
    SEARCHING = "searching"
    RETRYING = "retrying"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchEvent(BaseModel):
    """JSON-safe event envelope shared by persistence, tracing and SSE."""

    event_id: str = Field(default_factory=lambda: new_id("evt"))
    run_id: str = Field(..., min_length=1)
    task_id: str | None = None
    type: EventType
    timestamp: str = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


_SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|authorization|bearer|password|secret|token)\s*[:=]\s*[^\s,;]+"
)
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "password",
    "secret",
    "token",
}
_CONTENT_KEYS = {
    "content",
    "document",
    "documents",
    "markdown",
    "messages",
    "prompt",
    "raw_output",
    "report",
}


def redact_text(value: str, *, max_length: int = 500) -> str:
    """Remove credential-like values and bound free-form diagnostic text."""
    redacted = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    if len(redacted) > max_length:
        return f"{redacted[:max_length]}...[TRUNCATED]"
    return redacted


def redact_payload(value: Any, *, key: str = "", max_depth: int = 4) -> Any:
    """Redact secrets and large/private content before persistence or tracing."""
    if max_depth <= 0:
        return "[REDACTED_DEPTH]"
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS or any(
        marker in normalized_key for marker in ("api_key", "authorization", "password", "secret")
    ):
        return "[REDACTED]"
    if normalized_key in _CONTENT_KEYS:
        return "[REDACTED_CONTENT]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            str(child_key): redact_payload(child_value, key=str(child_key), max_depth=max_depth - 1)
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_payload(item, max_depth=max_depth - 1) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def encode_sse(event: ResearchEvent) -> str:
    """Encode one event using the SSE event/id/data wire format."""
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.type.value}\nid: {event.event_id}\ndata: {data}\n\n"


class EventPublisher:
    """Persist events and fan them out to a live SSE consumer."""

    def __init__(self, repository: Any, run_id: str):
        self.repository = repository
        self.run_id = run_id
        self._events: queue.Queue[ResearchEvent] = queue.Queue()
        self._closed = False

    def publish(
        self,
        event_type: EventType | str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> ResearchEvent:
        """Publish a redacted event; ``persist=False`` is for report-only SSE data."""
        event = ResearchEvent(
            run_id=self.run_id,
            task_id=task_id,
            type=EventType(event_type),
            payload=redact_payload(payload or {}),
        )
        if persist:
            try:
                self.repository.append_event(event)
            except Exception as error:
                # Observability must not turn a successful research run into a failure.
                logger.warning("Local event persistence failed: {}", str(error)[:300])
        if not self._closed:
            self._events.put(event)
        return event

    def drain(self) -> list[ResearchEvent]:
        """Return all currently queued events without blocking."""
        drained: list[ResearchEvent] = []
        while True:
            try:
                drained.append(self._events.get_nowait())
            except queue.Empty:
                return drained

    def iter_events(self, timeout: float = 0.25) -> Iterator[ResearchEvent]:
        """Yield queued events until the publisher is closed and drained."""
        while not self._closed or not self._events.empty():
            try:
                yield self._events.get(timeout=timeout)
            except queue.Empty:
                continue

    def close(self) -> None:
        self._closed = True


_PUBLISHERS: dict[str, EventPublisher] = {}
_PUBLISHERS_LOCK = threading.RLock()


def register_publisher(publisher: EventPublisher) -> None:
    with _PUBLISHERS_LOCK:
        _PUBLISHERS[publisher.run_id] = publisher


def unregister_publisher(run_id: str) -> None:
    with _PUBLISHERS_LOCK:
        _PUBLISHERS.pop(run_id, None)


def emit_event(
    state: dict[str, Any],
    event_type: EventType | str,
    *,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> ResearchEvent | None:
    """Emit through the run-scoped publisher when the graph runs under an API."""
    run = state.get("run") or {}
    run_id = str(run.get("run_id") or "")
    if not run_id:
        return None
    with _PUBLISHERS_LOCK:
        publisher = _PUBLISHERS.get(run_id)
    if publisher is None:
        return None
    return publisher.publish(event_type, task_id=task_id, payload=payload)
