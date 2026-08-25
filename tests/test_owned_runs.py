import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from src import main
from src.repository import NotFoundError, RepositoryError, SQLiteRepository
from src.session import create_session
from src.state import RunStatus, TaskItem, TaskPlan


def _plan(topic: str = "owned topic") -> TaskPlan:
    return TaskPlan(
        topic=topic,
        tasks=[TaskItem(id=1, title="Task", intent="Gather evidence", query="owned query")],
    )


class FakeOwnedGraph:
    def invoke(self, state, config=None):
        run = dict(state["run"])
        task_id = state["plan"]["tasks"][0]["task_id"]
        run["status"] = RunStatus.FAILED.value
        return {
            "run": run,
            "task_results": {
                task_id: {
                    "task_id": task_id,
                    "status": "failed",
                    "attempts": 1,
                    "query_history": ["owned query"],
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
                "report_id": "owned_report",
                "run_id": run["run_id"],
                "markdown": "# Owned report",
                "status": "failed",
            },
        }


def _register(client: TestClient, username: str) -> tuple[dict[str, str], str]:
    response = client.post("/auth/register", json={"username": username, "password": "correct-horse-42"})
    assert response.status_code == 201
    payload = response.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}, payload["user"]["user_id"]


def test_owned_plan_run_session_and_history_are_isolated(tmp_path, monkeypatch):
    repository = SQLiteRepository(tmp_path / "owned.db")
    graph = FakeOwnedGraph()
    monkeypatch.setattr(main, "_generate_valid_plan", lambda topic: _plan(topic))
    app = main.create_app(
        repository=repository,
        graph_factory=lambda **_kwargs: graph,
        initialize_services=False,
    )

    with TestClient(app) as client:
        alice_headers, alice_id = _register(client, "alice")
        bob_headers, _ = _register(client, "bob")

        planned = client.post("/plans", json={"topic": "Alice topic"}, headers=alice_headers)
        assert planned.status_code == 200
        plan = planned.json()["plan"]
        assert client.post(
            f"/plans/{plan['plan_id']}/versions/{plan['plan_version']}/confirm", headers=alice_headers
        ).status_code == 200
        created = client.post(
            "/runs",
            json={"plan_id": plan["plan_id"], "plan_version": plan["plan_version"]},
            headers=alice_headers,
        )
        assert created.status_code == 200
        run = created.json()
        run_id = run["run"]["run_id"]
        task_id = run["plan"]["tasks"][0]["task_id"]
        assert run["run"]["owner_id"] == alice_id
        assert run["run"]["document_scope"]["selection_mode"] == "none"
        assert repository.execution_state(run_id, owner_id=alice_id)["owner_id"] == alice_id

        forged = dict(run)
        forged["run"] = {**run["run"], "owner_id": "forged_owner"}
        with pytest.raises(RepositoryError, match="owner"):
            repository.persist_graph_result(run_id, forged, owner_id=alice_id)

        assert client.get(f"/plans/{plan['plan_id']}/versions/1", headers=bob_headers).status_code == 404
        assert client.get(f"/runs/{run_id}", headers=bob_headers).status_code == 404
        assert client.post(f"/runs/{run_id}/resume", headers=bob_headers).status_code == 404
        assert client.post(f"/runs/{run_id}/cancel", headers=bob_headers).status_code == 404
        assert client.post(f"/runs/{run_id}/tasks/{task_id}/retry", headers=bob_headers).status_code == 404
        assert client.get(f"/history/{run_id}", headers=bob_headers).status_code == 404
        assert client.get("/history", headers=bob_headers).json() == []

        session = create_session(alice_id)
        assert client.get(f"/sessions/{session.id}", headers=bob_headers).status_code == 404
        assert client.delete(f"/sessions/{session.id}", headers=bob_headers).status_code == 404
        assert client.post(f"/sessions/{session.id}/chat", json={"message": "hello"}, headers=bob_headers).status_code == 404
        assert client.post(
            "/research/stream",
            json={"topic": "unauthorized session", "session_id": session.id},
            headers=bob_headers,
        ).status_code == 404

        assert client.get("/history", headers=alice_headers).json()[0]["id"] == run_id

    repository.close()


def test_migration_keeps_legacy_rows_anonymous_and_is_idempotent(tmp_path):
    database_path = tmp_path / "legacy.db"
    legacy_plan = _plan("legacy topic")
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE plans (
            plan_id TEXT NOT NULL,
            plan_version INTEGER NOT NULL,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (plan_id, plan_version)
        )
        """
    )
    connection.execute(
        "INSERT INTO plans(plan_id, plan_version, topic, payload_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            legacy_plan.plan_id,
            legacy_plan.plan_version,
            legacy_plan.topic,
            json.dumps(legacy_plan.model_dump(mode="json")),
            RunStatus.PLANNED.value,
            "2026-08-25T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    repository = SQLiteRepository(database_path)
    with sqlite3.connect(database_path) as migrated:
        assert "owner_id" in {row[1] for row in migrated.execute("PRAGMA table_info(plans)")}
    assert repository.get_plan(legacy_plan.plan_id, legacy_plan.plan_version)["plan"]["topic"] == "legacy topic"
    user = repository.create_user("new_user", "not-used-in-this-test")
    with pytest.raises(NotFoundError):
        repository.get_plan(legacy_plan.plan_id, legacy_plan.plan_version, owner_id=user["user_id"])
    repository.close()

    reopened = SQLiteRepository(database_path)
    with sqlite3.connect(database_path) as migrated:
        assert list(row[1] for row in migrated.execute("PRAGMA table_info(plans)")).count("owner_id") == 1
    reopened.close()


def test_authenticated_apis_hide_legacy_unowned_records(tmp_path):
    repository = SQLiteRepository(tmp_path / "legacy-api.db")
    legacy_plan = repository.create_plan(_plan("legacy HTTP topic"))
    repository.confirm_plan(legacy_plan["plan"]["plan_id"], legacy_plan["plan"]["plan_version"])
    legacy_run = repository.create_run(legacy_plan["plan"]["plan_id"], legacy_plan["plan"]["plan_version"])
    app = main.create_app(repository=repository, initialize_services=False)

    with TestClient(app) as client:
        headers, _ = _register(client, "authenticated_reader")
        assert client.get(
            f"/plans/{legacy_plan['plan']['plan_id']}/versions/{legacy_plan['plan']['plan_version']}",
            headers=headers,
        ).status_code == 404
        assert client.get(f"/runs/{legacy_run['run']['run_id']}", headers=headers).status_code == 404
        assert client.get("/history", headers=headers).json() == []

    repository.close()
