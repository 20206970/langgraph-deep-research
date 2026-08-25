from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.config import DocumentConfig
from src.documents.jobs import DocumentWorker
from src.documents.models import DocumentVersionStatus, IngestionJobStatus
from src.documents.repository import DocumentQuotaExceededError, DocumentRepository
from src.documents.storage import StoredUpload
from src.repository import InvalidStateTransitionError, SQLiteRepository
from src.state import new_id


def _upload(filename: str = "paper.md", *, size: int = 16) -> StoredUpload:
    return StoredUpload(
        source_filename=filename,
        source_media_type="text/markdown",
        source_size=size,
        source_sha256="a" * 64,
        source_path=f"user_owner/doc_placeholder/{new_id('ver')}/source.md",
    )


@pytest.fixture
def document_context(tmp_path):
    config = DocumentConfig(
        storage_root=str(tmp_path / "private"),
        user_quota_bytes=100,
        job_lease_seconds=10,
        job_max_attempts=2,
    )
    core = SQLiteRepository(tmp_path / "research.db")
    owner = core.create_user("owner", "unused-password-hash")
    repository = DocumentRepository(core.database_path, config)
    yield repository, owner["user_id"], config
    core.close()


def _create_document(repository: DocumentRepository, owner_id: str, *, filename: str = "paper.md", size: int = 16):
    document_id = new_id("doc")
    version_id = new_id("ver")
    return repository.create_document(
        _upload(filename, size=size), owner_id=owner_id, document_id=document_id, version_id=version_id
    )


def test_same_name_uploads_are_distinct_and_usage_includes_all_versions(document_context):
    repository, owner_id, _ = document_context
    first = _create_document(repository, owner_id)
    second = _create_document(repository, owner_id)

    assert first["document"]["document_id"] != second["document"]["document_id"]
    assert first["versions"][0]["version_number"] == 1
    assert first["versions"][0]["status"] == DocumentVersionStatus.QUEUED.value
    assert first["jobs"][0]["status"] == IngestionJobStatus.QUEUED.value
    assert repository.usage(owner_id)["used_bytes"] == 32


def test_explicit_new_version_only_replaces_current_after_worker_success(document_context):
    repository, owner_id, _ = document_context
    first = _create_document(repository, owner_id)
    document_id = first["document"]["document_id"]
    first_version = first["versions"][0]["version_id"]

    claimed_first = repository.claim_next_job("worker-a")
    assert claimed_first is not None
    repository.mark_version_ready(document_id, first_version, owner_id=owner_id)
    ready = repository.get_document(document_id, owner_id=owner_id)
    assert ready["current_version"]["version_id"] == first_version
    assert ready["current_version"]["retrieval_enabled"] is True

    new_version_id = new_id("ver")
    queued = repository.create_version(
        document_id, _upload("paper-v2.md"), owner_id=owner_id, version_id=new_version_id
    )
    assert queued["current_version"]["version_id"] == first_version
    assert next(version for version in queued["versions"] if version["version_id"] == new_version_id)["is_current"] is False

    claimed_second = repository.claim_next_job("worker-b")
    assert claimed_second is not None and claimed_second["version_id"] == new_version_id
    repository.fail_job(claimed_second["job_id"], error_code="CONVERSION_FAILED", error_summary="controlled failure")
    failed = repository.get_document(document_id, owner_id=owner_id)
    assert failed["current_version"]["version_id"] == first_version
    assert next(version for version in failed["versions"] if version["version_id"] == first_version)["status"] == "ready"

    retried = repository.retry_version(document_id, new_version_id, owner_id=owner_id)
    assert next(job for job in retried["jobs"] if job["version_id"] == new_version_id)["attempt"] == 0
    claimed_retry = repository.claim_next_job("worker-c")
    repository.mark_version_ready(document_id, claimed_retry["version_id"], owner_id=owner_id)
    promoted = repository.get_document(document_id, owner_id=owner_id)
    assert promoted["current_version"]["version_id"] == new_version_id
    assert next(version for version in promoted["versions"] if version["version_id"] == first_version)["status"] == "archived"


def test_quota_includes_deleted_documents_and_rejects_overage(document_context):
    repository, owner_id, _ = document_context
    document = _create_document(repository, owner_id, size=80)
    repository.delete_document(document["document"]["document_id"], owner_id=owner_id)

    with pytest.raises(DocumentQuotaExceededError):
        _create_document(repository, owner_id, filename="next.md", size=21)
    assert repository.usage(owner_id)["used_bytes"] == 80


def test_delete_restore_requeues_interrupted_work_and_honors_retention(document_context):
    repository, owner_id, config = document_context
    document = _create_document(repository, owner_id)
    document_id = document["document"]["document_id"]
    version_id = document["versions"][0]["version_id"]
    claimed = repository.claim_next_job("worker-a")
    assert claimed is not None

    deleted = repository.delete_document(document_id, owner_id=owner_id)
    assert deleted["document"]["deleted_at"] is not None
    assert deleted["versions"][0]["status"] == "deleted"
    assert deleted["jobs"][0]["status"] == "cancelled"

    restored = repository.restore_document(document_id, owner_id=owner_id)
    assert restored["document"]["deleted_at"] is None
    assert restored["versions"][0]["status"] == "queued"
    assert restored["jobs"][0]["status"] == "queued"
    assert repository.claim_next_job("worker-b")["version_id"] == version_id

    repository.delete_document(document_id, owner_id=owner_id)
    past_retention = datetime.now(timezone.utc) + timedelta(days=config.purge_retention_days + 1)
    with pytest.raises(InvalidStateTransitionError, match="recovery window"):
        repository.restore_document(document_id, owner_id=owner_id, now=past_retention)


def test_job_claim_is_exclusive_and_expired_lease_recovers(document_context):
    repository, owner_id, config = document_context
    document = _create_document(repository, owner_id)
    base_time = datetime(2026, 8, 25, tzinfo=timezone.utc)

    def claim(worker_id: str):
        return DocumentRepository(repository.database_path, config).claim_next_job(worker_id, now=base_time)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["worker-a", "worker-b"]))
    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0]["stage"] == "converting"

    recovered = repository.claim_next_job("worker-c", now=base_time + timedelta(seconds=config.job_lease_seconds + 1))
    assert recovered is not None
    assert recovered["job_id"] == claimed[0]["job_id"]
    assert recovered["attempt"] == 2


def test_expired_max_attempt_and_explicit_retry_are_recoverable(document_context):
    repository, owner_id, config = document_context
    document = _create_document(repository, owner_id)
    base_time = datetime(2026, 8, 25, tzinfo=timezone.utc)
    first = repository.claim_next_job("worker-a", now=base_time)
    second = repository.claim_next_job("worker-b", now=base_time + timedelta(seconds=config.job_lease_seconds + 1))
    assert second is not None and second["attempt"] == 2
    assert repository.claim_next_job("worker-c", now=base_time + timedelta(seconds=2 * config.job_lease_seconds + 2)) is None

    failed = repository.get_document(document["document"]["document_id"], owner_id=owner_id)
    version = failed["versions"][0]
    assert version["status"] == "failed"
    retried = repository.retry_version(document["document"]["document_id"], version["version_id"], owner_id=owner_id)
    assert retried["jobs"][0]["attempt"] == 0
    assert repository.claim_next_job("worker-d") is not None


def test_worker_marks_success_and_redacts_handler_failure(document_context):
    repository, owner_id, _ = document_context
    document = _create_document(repository, owner_id)
    worker = DocumentWorker(repository, lambda _job: None, worker_id="worker-ok")
    assert worker.run_once().status == "succeeded"
    assert repository.get_document(document["document"]["document_id"], owner_id=owner_id)["current_version"]["status"] == "ready"

    failed_document = _create_document(repository, owner_id, filename="failure.md")

    def fail(_job):
        raise RuntimeError("conversion failed: api_key=private-value")

    failed = DocumentWorker(repository, fail, worker_id="worker-fail").run_once()
    assert failed.status == "failed"
    job = repository.get_document(failed_document["document"]["document_id"], owner_id=owner_id)["jobs"][0]
    assert job["error_code"] == "INGESTION_HANDLER_FAILED"
    assert "private-value" not in job["error_summary"]


def test_worker_does_not_resurrect_a_document_deleted_during_processing(document_context):
    repository, owner_id, _ = document_context
    document = _create_document(repository, owner_id, filename="race.md")
    document_id = document["document"]["document_id"]

    def delete_during_handler(_job):
        repository.delete_document(document_id, owner_id=owner_id)

    result = DocumentWorker(repository, delete_during_handler, worker_id="worker-race").run_once()
    assert result.status == "cancelled"
    deleted = repository.get_document(document_id, owner_id=owner_id)
    assert deleted["document"]["deleted_at"] is not None
    assert deleted["versions"][0]["status"] == "deleted"
