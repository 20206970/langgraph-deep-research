import json

import pytest
from fastapi.testclient import TestClient

from src import main
from src.config import TracingConfig
from src.events import EventPublisher, EventType, ResearchEvent, encode_sse, redact_payload
from src.repository import SQLiteRepository
from src.tracing import build_trace_callbacks
from src.state import TaskItem, TaskPlan


def _plan(topic="stream topic"):
    return TaskPlan(
        topic=topic,
        tasks=[TaskItem(id=1, title="Task", intent="Gather evidence", query="stream query")],
    )


def _register(client: TestClient, username: str = "stream_user") -> tuple[dict[str, str], str]:
    response = client.post("/auth/register", json={"username": username, "password": "correct-horse-42"})
    assert response.status_code == 201
    payload = response.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}, payload["user"]["user_id"]


def test_event_redaction_and_sse_encoding():
    payload = redact_payload(
        {
            "authorization": "Bearer top-secret",
            "error_message": "request failed with api_key=private-value",
            "report": "private report body",
            "source_path": "private/owner/document/source.md",
            "evidence_excerpt": "private paper text",
            "attempt": 2,
        }
    )
    assert payload["authorization"] == "[REDACTED]"
    assert "private-value" not in payload["error_message"]
    assert payload["report"] == "[REDACTED_CONTENT]"
    assert payload["source_path"] == "[REDACTED_CONTENT]"
    assert payload["evidence_excerpt"] == "[REDACTED_CONTENT]"

    event = ResearchEvent(
        run_id="run_test",
        task_id="task_test",
        type=EventType.TASK_COMPLETED,
        payload={"status": "succeeded", "attempt": 2},
    )
    encoded = encode_sse(event)
    assert encoded.startswith("event: task_completed\nid: ")
    assert "data: {" in encoded
    assert encoded.endswith("\n\n")
    data_line = next(line for line in encoded.splitlines() if line.startswith("data: "))
    assert json.loads(data_line[6:])["event_id"] == event.event_id


def test_publisher_persists_and_reads_structured_events(tmp_path):
    repository = SQLiteRepository(tmp_path / "events.db")
    publisher = EventPublisher(repository, "run_test")
    event = publisher.publish(
        EventType.TASK_FAILED,
        task_id="task_test",
        payload={"status": "failed", "error_message": "safe summary"},
    )

    assert publisher.drain() == [event]
    persisted = repository.list_events("run_test", task_id="task_test")
    assert len(persisted) == 1
    assert persisted[0].event_id == event.event_id
    assert persisted[0].type == EventType.TASK_FAILED
    publisher.close()
    repository.close()


def test_langsmith_is_opt_in_and_failure_isolated(monkeypatch):
    assert build_trace_callbacks(TracingConfig(), {"run_id": "run_test"}) == []

    import langsmith

    def fail_client(**_kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(langsmith, "Client", fail_client)
    callbacks = build_trace_callbacks(
        TracingConfig(enabled=True, api_key="test-key"),
        {"run_id": "run_test"},
    )
    assert callbacks == []


class FakeStreamGraph:
    def __init__(self):
        self.config = None
        self.run_id = None

    def stream(self, state, config=None, stream_mode=None):
        self.config = config
        self.run_id = state["run"]["run_id"]
        assert stream_mode == "values"
        task_id = state["plan"]["tasks"][0]["task_id"]
        run = dict(state["run"])
        run["status"] = "succeeded"
        yield {
            "run": run,
            "plan": state["plan"],
            "task_results": {
                task_id: {
                    "task_id": task_id,
                    "status": "succeeded",
                    "attempts": 1,
                    "query_history": ["stream query"],
                    "summary": "stream summary",
                    "source_ids": [],
                    "claims": [],
                    "parse_status": "valid",
                }
            },
            "sources": {},
            "task_source_refs": {},
            "report_artifact": {
                "report_id": "stream_report",
                "run_id": run["run_id"],
                "markdown": "# Stream report",
                "status": "succeeded",
            },
        }


def test_stream_endpoint_emits_standard_sse_and_persists_event(tmp_path, monkeypatch):
    repository = SQLiteRepository(tmp_path / "stream.db")
    fake_graph = FakeStreamGraph()
    monkeypatch.setattr(main, "_generate_valid_plan", lambda _topic: _plan())
    app = main.create_app(
        repository=repository,
        graph_factory=lambda **_kwargs: fake_graph,
        initialize_services=False,
    )

    with TestClient(app) as client:
        headers, owner_id = _register(client)
        response = client.post("/research/stream", json={"topic": "stream topic"}, headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: plan_confirmed" in response.text
    assert "event: completed" in response.text
    assert "id: " in response.text
    assert "# Stream report" in response.text
    assert "data: {\"topic\"" not in response.text
    assert fake_graph.config["configurable"]["thread_id"] == fake_graph.run_id
    assert any(
        event.type == EventType.PLAN_CONFIRMED
        for event in repository.list_events(fake_graph.run_id, owner_id=owner_id)
    )
    repository.close()
