from pathlib import Path

from fastapi.testclient import TestClient

from src import main
from src.config import DocumentConfig
from src.documents.repository import DocumentRepository
from src.documents.storage import DocumentStorage
from src.repository import SQLiteRepository


class _IndexLifecycleRecorder:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def sync_document_state(self, document_id: str, *, owner_id: str):
        self.calls.append((document_id, owner_id))


def _setup(tmp_path, *, document_index=None):
    config = DocumentConfig(storage_root=str(tmp_path / "private"), user_quota_bytes=1_024, max_file_bytes=512)
    core = SQLiteRepository(tmp_path / "research.db")
    document_repository = DocumentRepository(core.database_path, config)
    app = main.create_app(
        repository=core,
        document_repository=document_repository,
        document_storage=DocumentStorage(config),
        document_index=document_index,
        initialize_services=False,
    )
    return app, core, document_repository, config


def _register(client: TestClient, username: str):
    response = client.post("/auth/register", json={"username": username, "password": "correct-horse-42"})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_document_api_uploads_queues_lists_and_isolates_users(tmp_path):
    app, core, _repository, config = _setup(tmp_path)
    with TestClient(app) as client:
        alice = _register(client, "alice-docs")
        bob = _register(client, "bob-docs")
        assert client.post("/documents", files={"file": ("paper.md", b"# Paper", "text/markdown")}).status_code == 401

        uploaded = client.post(
            "/documents", files={"file": ("paper.md", b"# Paper", "text/markdown")}, headers=alice
        )
        assert uploaded.status_code == 201
        payload = uploaded.json()
        document_id = payload["document_id"]
        version_id = payload["current_version"]["version_id"]
        assert payload["current_version"]["status"] == "queued"
        assert payload["jobs"][0]["status"] == "queued"
        assert "source_path" not in str(payload)
        assert (Path(config.storage_root) / "user_").exists() is False

        listed = client.get("/documents", headers=alice)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["document_id"] == document_id
        assert client.get("/documents/usage", headers=alice).json()["used_bytes"] == len(b"# Paper")
        assert client.get(f"/documents/{document_id}", headers=bob).status_code == 404
        assert client.post(
            f"/documents/{document_id}/versions", files={"file": ("v2.md", b"# V2", "text/markdown")}, headers=bob
        ).status_code == 404

        version_upload = client.post(
            f"/documents/{document_id}/versions",
            files={"file": ("v2.md", b"# V2", "text/markdown")},
            headers=alice,
        )
        assert version_upload.status_code == 201
        versions = client.get(f"/documents/{document_id}/versions", headers=alice).json()
        assert [version["version_number"] for version in versions] == [2, 1]
        assert versions[0]["version_id"] != version_id

        deleted = client.delete(f"/documents/{document_id}", headers=alice)
        assert deleted.status_code == 200
        assert deleted.json()["deleted_at"] is not None
        assert client.get("/documents", headers=alice).json()["total"] == 0
        assert client.get("/documents?include_deleted=true", headers=alice).json()["total"] == 1
        assert client.post(f"/documents/{document_id}/restore", headers=alice).status_code == 200
    core.close()


def test_document_api_reports_upload_validation_errors_without_leaking_paths(tmp_path):
    app, core, _repository, _config = _setup(tmp_path)
    with TestClient(app) as client:
        headers = _register(client, "validation-docs")
        bad_pdf = client.post(
            "/documents", files={"file": ("paper.pdf", b"not pdf", "application/pdf")}, headers=headers
        )
        assert bad_pdf.status_code == 415
        assert "private" not in bad_pdf.json()["detail"]

        unsupported = client.post(
            "/documents", files={"file": ("paper.txt", b"text", "text/plain")}, headers=headers
        )
        assert unsupported.status_code == 415
    core.close()


def test_document_delete_and_restore_sync_document_vector_lifecycle(tmp_path):
    index = _IndexLifecycleRecorder()
    app, core, _repository, _config = _setup(tmp_path, document_index=index)
    with TestClient(app) as client:
        headers = _register(client, "lifecycle-docs")
        uploaded = client.post(
            "/documents", files={"file": ("paper.md", b"# Paper", "text/markdown")}, headers=headers
        ).json()
        document_id = uploaded["document_id"]

        assert client.delete(f"/documents/{document_id}", headers=headers).status_code == 200
        assert client.post(f"/documents/{document_id}/restore", headers=headers).status_code == 200
        assert [call[0] for call in index.calls] == [document_id, document_id]
    core.close()
