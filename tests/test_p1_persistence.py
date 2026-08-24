from pathlib import Path
from typing_extensions import TypedDict

import pytest
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph

from src import main
from src.repository import InvalidStateTransitionError, SQLiteRepository
from src.state import ResearchRun, RunStatus, TaskItem, TaskPlan, TaskStatus


def _plan(topic="durable topic"):
    return TaskPlan(
        topic=topic,
        tasks=[TaskItem(id=1, title="Task", intent="Gather evidence", query="durable query")],
    )


def _failed_graph_result(run, plan, attempt=1):
    task_id = plan["tasks"][0]["task_id"]
    graph_run = dict(run)
    graph_run["status"] = "failed"
    return {
        "run": graph_run,
        "task_results": {
            task_id: {
                "task_id": task_id,
                "status": "failed",
                "attempts": attempt,
                "query_history": ["durable query"],
                "summary": "任务未完成：受控失败。",
                "source_ids": [],
                "claims": [],
                "error_code": "CONTROLLED_FAILURE",
                "error_message": "controlled failure",
                "parse_status": "rejected",
            }
        },
        "sources": {},
        "task_source_refs": {},
        "report_artifact": {
            "report_id": f"report_failed_{attempt}",
            "run_id": run["run_id"],
            "markdown": "# Failed report",
            "status": "failed",
        },
    }


def test_repository_versions_plans_and_restores_checkpoint_from_same_database(tmp_path):
    database_path = tmp_path / "research.db"
    repository = SQLiteRepository(database_path)
    first = repository.create_plan(_plan())

    with pytest.raises(InvalidStateTransitionError, match="confirmed plan"):
        repository.create_run(first["plan"]["plan_id"], first["plan"]["plan_version"])

    second = repository.update_plan(first["plan"]["plan_id"], 1, _plan("edited topic"))
    assert second["plan"]["plan_id"] == first["plan"]["plan_id"]
    assert second["plan"]["plan_version"] == 2
    assert repository.get_plan(first["plan"]["plan_id"], 1)["plan"]["topic"] == "durable topic"

    repository.confirm_plan(second["plan"]["plan_id"], second["plan"]["plan_version"])
    run = repository.create_run(second["plan"]["plan_id"], second["plan"]["plan_version"])

    class CheckpointState(TypedDict):
        value: int

    workflow = StateGraph(CheckpointState)
    workflow.add_node("increment", lambda state: {"value": state["value"] + 1})
    workflow.add_edge(START, "increment")
    workflow.add_edge("increment", END)
    graph = workflow.compile(checkpointer=repository.checkpointer)
    config = {"configurable": {"thread_id": run["run"]["thread_id"]}}
    assert graph.invoke({"value": 1}, config=config)["value"] == 2
    repository.close()

    restored_repository = SQLiteRepository(database_path)
    restored_graph = workflow.compile(checkpointer=restored_repository.checkpointer)
    assert restored_graph.get_state(config).values["value"] == 2
    restored_repository.close()


def test_failed_task_retry_only_replaces_target_attempt_and_versions_report(tmp_path):
    repository = SQLiteRepository(tmp_path / "retry.db")
    plan_record = repository.create_plan(_plan())
    repository.confirm_plan(plan_record["plan"]["plan_id"], 1)
    created = repository.create_run(plan_record["plan"]["plan_id"], 1)
    run_id = created["run"]["run_id"]
    repository.mark_run_running(run_id)
    repository.persist_graph_result(run_id, _failed_graph_result(created["run"], created["plan"]))

    retry_state = repository.prepare_task_retry(run_id, created["plan"]["tasks"][0]["task_id"])

    assert retry_state["retry_task_id"] == created["plan"]["tasks"][0]["task_id"]
    assert retry_state["task_results"][retry_state["retry_task_id"]]["attempts"] == 1
    assert retry_state["run"]["status"] == RunStatus.CONFIRMED.value

    graph_run = ResearchRun.model_validate(retry_state["run"]).model_copy(update={"status": RunStatus.SUCCEEDED})
    task_id = retry_state["retry_task_id"]
    retried = {
        "run": graph_run.model_dump(mode="json"),
        "task_results": {
            task_id: {
                "task_id": task_id,
                "status": TaskStatus.SUCCEEDED.value,
                "attempts": 2,
                "query_history": ["durable query", "durable query"],
                "summary": "经过重试后获得可验证证据。" * 8,
                "source_ids": [],
                "claims": [],
                "parse_status": "valid",
            }
        },
        "sources": {},
        "task_source_refs": {},
        "report_artifact": {
            "report_id": "report_retry_2",
            "run_id": run_id,
            "markdown": "# Retried report",
            "status": "succeeded",
        },
    }
    persisted = repository.persist_graph_result(run_id, retried)

    assert persisted["run"]["status"] == RunStatus.SUCCEEDED.value
    assert persisted["task_runs"][0]["attempt"] == 2
    assert len(persisted["report_versions"]) == 2
    with pytest.raises(InvalidStateTransitionError, match="only failed tasks"):
        repository.prepare_task_retry(run_id, task_id)
    repository.close()


class FakeDurableGraph:
    def __init__(self):
        self.inputs = []

    def invoke(self, state, config=None):
        self.inputs.append((state, config))
        run = dict(state["run"])
        task_id = state["plan"]["tasks"][0]["task_id"]
        prior_attempts = int(state["task_results"][task_id].get("attempts") or 0)
        attempt = prior_attempts + 1
        failed = len(self.inputs) == 1
        run["status"] = "failed" if failed else "succeeded"
        return {
            "run": run,
            "task_results": {
                task_id: {
                    "task_id": task_id,
                    "status": "failed" if failed else "succeeded",
                    "attempts": attempt,
                    "query_history": ["durable query"],
                    "summary": "任务未完成：受控失败。" if failed else "重试后获得了可验证结论。" * 10,
                    "source_ids": [],
                    "claims": [],
                    "error_code": "CONTROLLED_FAILURE" if failed else None,
                    "error_message": "controlled failure" if failed else None,
                    "parse_status": "rejected" if failed else "valid",
                }
            },
            "sources": {},
            "task_source_refs": {},
            "report_artifact": {
                "report_id": f"report_api_{attempt}",
                "run_id": run["run_id"],
                "markdown": "# Failed" if failed else "# Recovered",
                "status": run["status"],
            },
        }


def test_plan_confirmation_api_blocks_execution_until_confirmed_and_retries_failed_task(tmp_path, monkeypatch):
    repository = SQLiteRepository(tmp_path / "api.db")
    fake_graph = FakeDurableGraph()
    monkeypatch.setattr(main, "_generate_valid_plan", lambda topic: _plan(topic))
    app = main.create_app(
        repository=repository,
        graph_factory=lambda **_kwargs: fake_graph,
        initialize_services=False,
    )

    with TestClient(app) as client:
        planned = client.post("/plans", json={"topic": "API topic"})
        assert planned.status_code == 200
        plan = planned.json()["plan"]

        unconfirmed = client.post("/runs", json={"plan_id": plan["plan_id"], "plan_version": plan["plan_version"]})
        assert unconfirmed.status_code == 409
        assert fake_graph.inputs == []

        confirmed = client.post(f"/plans/{plan['plan_id']}/versions/1/confirm")
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "confirmed"

        first_run = client.post("/runs", json={"plan_id": plan["plan_id"], "plan_version": 1})
        assert first_run.status_code == 200
        run = first_run.json()
        task_id = run["plan"]["tasks"][0]["task_id"]
        assert run["run"]["status"] == "failed"
        assert len(run["report_versions"]) == 1

        retried = client.post(f"/runs/{run['run']['run_id']}/tasks/{task_id}/retry")
        assert retried.status_code == 200
        assert retried.json()["run"]["status"] == "succeeded"
        assert retried.json()["task_runs"][0]["attempt"] == 2
        assert len(retried.json()["report_versions"]) == 2
        assert fake_graph.inputs[1][0]["retry_task_id"] == task_id

    repository.close()


class FakeResumeGraph:
    def __init__(self, repository):
        self.repository = repository
        self.invocations = []

    def get_state(self, _config):
        return type("Snapshot", (), {"values": {"checkpoint": "present"}})()

    def invoke(self, state, config=None):
        self.invocations.append((state, config))
        assert state is None
        run_id = config["configurable"]["thread_id"]
        persisted = self.repository.get_run(run_id)
        run = dict(persisted["run"])
        run["status"] = RunStatus.SUCCEEDED.value
        task = persisted["plan"]["tasks"][0]
        task_id = task["task_id"]
        return {
            "run": run,
            "task_results": {
                task_id: {
                    "task_id": task_id,
                    "status": TaskStatus.SUCCEEDED.value,
                    "attempts": 1,
                    "query_history": [task["query"]],
                    "summary": "从 checkpoint 恢复后完成任务。" * 10,
                    "source_ids": [],
                    "claims": [],
                    "parse_status": "valid",
                }
            },
            "sources": {},
            "task_source_refs": {},
            "report_artifact": {
                "report_id": "report_resumed",
                "run_id": run_id,
                "markdown": "# Resumed report",
                "status": "succeeded",
            },
        }


def test_resume_uses_existing_checkpoint_and_cancel_is_terminal(tmp_path):
    repository = SQLiteRepository(tmp_path / "resume-cancel.db")
    plan_record = repository.create_plan(_plan())
    repository.confirm_plan(plan_record["plan"]["plan_id"], 1)
    created = repository.create_run(plan_record["plan"]["plan_id"], 1)
    run_id = created["run"]["run_id"]
    repository.mark_run_running(run_id)
    fake_graph = FakeResumeGraph(repository)
    app = main.create_app(
        repository=repository,
        graph_factory=lambda **_kwargs: fake_graph,
        initialize_services=False,
    )

    with TestClient(app) as client:
        resumed = client.post(f"/runs/{run_id}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["run"]["status"] == RunStatus.SUCCEEDED.value
        assert fake_graph.invocations[0][0] is None
        assert fake_graph.invocations[0][1]["configurable"]["thread_id"] == run_id

        next_run = repository.create_run(plan_record["plan"]["plan_id"], 1)
        cancelled = client.post(f"/runs/{next_run['run']['run_id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["run"]["status"] == RunStatus.CANCELLED.value
        assert cancelled.json()["task_runs"][0]["status"] == TaskStatus.CANCELLED.value

        # A late graph result must not resurrect a cancelled run.
        discarded = repository.persist_graph_result(
            next_run["run"]["run_id"],
            _failed_graph_result(next_run["run"], next_run["plan"]),
        )
        assert discarded["run"]["status"] == RunStatus.CANCELLED.value
        assert discarded["report_versions"] == []
        assert client.post(f"/runs/{next_run['run']['run_id']}/resume").status_code == 409

    repository.close()
