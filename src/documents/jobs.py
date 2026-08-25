"""Worker-safe ingestion-job claiming and execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from src.repository import InvalidStateTransitionError

from .repository import DocumentRepository


class IngestionHandler(Protocol):
    """The P2.3 conversion pipeline will implement this one-job callback."""

    def __call__(self, job: dict[str, Any]) -> None:
        """Process one exclusively leased job or raise a safe exception."""


@dataclass(frozen=True)
class JobRunResult:
    job_id: str | None
    status: str
    error_code: str | None = None


class DocumentWorker:
    """Claim at most one job at a time; lifecycle transitions remain owned by the repository."""

    def __init__(self, repository: DocumentRepository, handler: IngestionHandler, *, worker_id: str):
        self.repository = repository
        self.handler = handler
        self.worker_id = worker_id

    def run_once(self) -> JobRunResult:
        job = self.repository.claim_next_job(self.worker_id)
        if job is None:
            return JobRunResult(job_id=None, status="idle")
        try:
            self.handler(job)
            self.repository.mark_version_ready(
                str(job["document_id"]), str(job["version_id"]), owner_id=str(job["owner_id"])
            )
        except Exception as error:
            try:
                self.repository.fail_job(
                    str(job["job_id"]),
                    error_code="INGESTION_HANDLER_FAILED",
                    error_summary=str(error),
                )
            except InvalidStateTransitionError:
                # A delete request may cancel the leased job while its handler is still running.
                return JobRunResult(job_id=str(job["job_id"]), status="cancelled", error_code="DOCUMENT_CANCELLED")
            return JobRunResult(job_id=str(job["job_id"]), status="failed", error_code="INGESTION_HANDLER_FAILED")
        return JobRunResult(job_id=str(job["job_id"]), status="succeeded")

    def run_forever(self, *, poll_seconds: float = 1.0, should_stop: Callable[[], bool] | None = None) -> None:
        """Run until asked to stop; an idle queue is polled without holding SQLite transactions."""

        import time

        while not (should_stop and should_stop()):
            result = self.run_once()
            if result.status == "idle":
                time.sleep(poll_seconds)
