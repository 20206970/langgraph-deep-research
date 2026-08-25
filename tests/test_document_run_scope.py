from fastapi.testclient import TestClient

from src import main
from src.config import DocumentConfig
from src.documents.repository import DocumentRepository
from src.documents.storage import DocumentStorage
from src.repository import SQLiteRepository
from src.state import RunStatus, TaskItem, TaskPlan


def _plan(topic):
    return TaskPlan(topic=topic, tasks=[TaskItem(id=1, title="Task", intent="Use evidence", query="query")])


def _register(client, username):
    response = client.post("/auth/register", json={"username": username, "password": "correct-horse-42"})
    assert response.status_code == 201
    payload = response.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}, payload["user"]["user_id"]


class _SnapshotGraph:
    def __init__(self):
        self.inputs = []

    def invoke(self, state, config=None):
        self.inputs.append(state)
        run = dict(state["run"])
        run["status"] = RunStatus.FAILED.value
        task_id = state["plan"]["tasks"][0]["task_id"]
        return {
            "run": run,
            "task_results": {
                task_id: {
                    "task_id": task_id,
                    "status": "failed",
                    "attempts": 1,
                    "query_history": ["query"],
                    "summary": "controlled failure",
                    "source_ids": [],
                    "claims": [],
                    "error_code": "CONTROLLED_FAILURE",
                    "parse_status": "rejected",
                }
            },
            "sources": {},
            "task_source_refs": {},
            "report_artifact": {
                "report_id": f"report_{len(self.inputs)}",
                "run_id": run["run_id"],
                "markdown": "# Report",
                "status": "failed",
            },
        }


def _ready_document(client, repository, owner_id, headers, filename):
    response = client.post("/documents", files={"file": (filename, b"# Paper", "text/markdown")}, headers=headers)
    assert response.status_code == 201
    payload = response.json()
    version_id = payload["current_version"]["version_id"]
    repository.mark_version_ready(payload["document_id"], version_id, owner_id=owner_id)
    return payload["document_id"], version_id


def test_run_scope_is_authorized_once_and_reused_for_retry_after_a_new_version(tmp_path, monkeypatch):
    core = SQLiteRepository(tmp_path / "runs.db")
    config = DocumentConfig(storage_root=str(tmp_path / "private"), chroma_persist_dir=str(tmp_path / "chroma"))
    documents = DocumentRepository(core.database_path, config)
    graph = _SnapshotGraph()
    monkeypatch.setattr(main, "_generate_valid_plan", _plan)
    app = main.create_app(
        repository=core,
        graph_factory=lambda **_kwargs: graph,
        initialize_services=False,
        document_repository=documents,
        document_storage=DocumentStorage(config),
    )

    with TestClient(app) as client:
        alice_headers, alice_id = _register(client, "snapshot-alice")
        bob_headers, bob_id = _register(client, "snapshot-bob")
        document_id, version_one = _ready_document(client, documents, alice_id, alice_headers, "paper.md")
        bob_document_id, _ = _ready_document(client, documents, bob_id, bob_headers, "bob.md")

        planned = client.post("/plans", json={"topic": "scope topic"}, headers=alice_headers).json()["plan"]
        assert client.post(
            f"/plans/{planned['plan_id']}/versions/{planned['plan_version']}/confirm", headers=alice_headers
        ).status_code == 200

        cross_owner = client.post(
            "/runs",
            json={
                "plan_id": planned["plan_id"],
                "plan_version": planned["plan_version"],
                "document_ids": [bob_document_id],
            },
            headers=alice_headers,
        )
        assert cross_owner.status_code == 404

        created = client.post(
            "/runs",
            json={
                "plan_id": planned["plan_id"],
                "plan_version": planned["plan_version"],
                "document_ids": [document_id],
            },
            headers=alice_headers,
        )
        assert created.status_code == 200
        run = created.json()
        run_id = run["run"]["run_id"]
        task_id = run["plan"]["tasks"][0]["task_id"]
        assert run["run"]["document_scope"]["version_ids"] == [version_one]
        assert graph.inputs[0]["document_scope"]["version_ids"] == [version_one]

        version_response = client.post(
            f"/documents/{document_id}/versions",
            files={"file": ("paper-v2.md", b"# Paper V2", "text/markdown")},
            headers=alice_headers,
        )
        assert version_response.status_code == 201
        version_two = next(
            version["version_id"]
            for version in version_response.json()["versions"]
            if version["version_id"] != version_one
        )
        documents.mark_version_ready(document_id, version_two, owner_id=alice_id)

        retried = client.post(f"/runs/{run_id}/tasks/{task_id}/retry", headers=alice_headers)
        assert retried.status_code == 200
        assert graph.inputs[1]["document_scope"]["version_ids"] == [version_one]
        assert version_two not in graph.inputs[1]["document_scope"]["version_ids"]

    core.close()
