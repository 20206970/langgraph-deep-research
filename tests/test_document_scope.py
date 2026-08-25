import pytest

from src.config import DocumentConfig
from src.documents.repository import DocumentRepository
from src.documents.storage import StoredUpload
from src.repository import InvalidStateTransitionError, NotFoundError, SQLiteRepository
from src.state import new_id


def _create_document(repository, owner_id, *, ready=True):
    document_id, version_id = new_id("doc"), new_id("ver")
    repository.create_document(
        StoredUpload(
            source_filename="paper.md",
            source_media_type="text/markdown",
            source_size=10,
            source_sha256="a" * 64,
            source_path=f"private/{document_id}/{version_id}/source.md",
        ),
        owner_id=owner_id,
        document_id=document_id,
        version_id=version_id,
    )
    if ready:
        repository.mark_version_ready(document_id, version_id, owner_id=owner_id)
    return document_id, version_id


def test_document_scope_resolves_only_owned_ready_current_versions(tmp_path):
    core = SQLiteRepository(tmp_path / "scope.db")
    owner_a = core.create_user("scope-a", "hash")["user_id"]
    owner_b = core.create_user("scope-b", "hash")["user_id"]
    repository = DocumentRepository(core.database_path, DocumentConfig(storage_root=str(tmp_path / "private")))
    try:
        doc_a1, version_a1 = _create_document(repository, owner_a)
        _doc_a2, version_a2 = _create_document(repository, owner_a)
        doc_failed, _version_failed = _create_document(repository, owner_a, ready=False)
        doc_b, _version_b = _create_document(repository, owner_b)

        explicit = repository.resolve_document_scope(owner_id=owner_a, document_ids=[doc_a1])
        assert explicit.selection_mode == "explicit"
        assert explicit.version_ids == [version_a1]

        all_documents = repository.resolve_document_scope(owner_id=owner_a, use_all_my_documents=True)
        assert all_documents.selection_mode == "all_my_documents"
        assert set(all_documents.version_ids) == {version_a1, version_a2}

        with pytest.raises(InvalidStateTransitionError, match="not ready"):
            repository.resolve_document_scope(owner_id=owner_a, document_ids=[doc_failed])
        with pytest.raises(NotFoundError):
            repository.resolve_document_scope(owner_id=owner_a, document_ids=[doc_b])

        repository.delete_document(doc_a1, owner_id=owner_a)
        with pytest.raises(InvalidStateTransitionError, match="not ready"):
            repository.resolve_document_scope(owner_id=owner_a, document_ids=[doc_a1])
    finally:
        core.close()
