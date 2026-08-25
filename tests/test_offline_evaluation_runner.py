import json

import pytest

from src.evaluation.dataset import load_evaluation_dataset
from src.evaluation.runner import OfflineEvaluationRunner
from src.graph import research

from tests.test_evaluation_fixtures import _dataset_dir


class FakeSnapshotGraph:
    def __init__(self, source_id, inspect_fixture=False):
        self.source_id = source_id
        self.inspect_fixture = inspect_fixture

    def invoke(self, _payload):
        if self.inspect_fixture:
            tools = research.create_summarizer_agent(object())
            assert [tool.name for tool in tools] == ["search_web", "search_papers"]
            tool_payload = json.loads(tools[0].invoke({"query": "fixture check"}))
            assert [item["source_id"] for item in tool_payload["results"]] == ["src_allowed"]
            assert research.get_long_term_memory() is None
        return {
            "run": {"run_id": "run_fake", "status": "succeeded"},
            "plan": {
                "parse_status": "valid",
                "tasks": [{"task_id": "task_1", "title": "Offline", "intent": "offline fixture", "query": "offline"}],
            },
            "tasks": [{"task_id": "task_1", "title": "Offline", "intent": "offline fixture", "query": "offline"}],
            "task_results": {
                "task_1": {
                    "task_id": "task_1",
                    "status": "succeeded",
                    "attempts": 1,
                    "summary": "A summary grounded in the fixture source.",
                    "source_ids": [self.source_id],
                    "claims": [
                        {
                            "claim_id": "claim_1",
                            "task_id": "task_1",
                            "text": "A source-bound claim.",
                            "source_ids": [self.source_id],
                            "evidence_status": "supported",
                        }
                    ],
                    "latency_ms": 12,
                    "token_usage": {"input_tokens": 10, "output_tokens": 5},
                }
            },
            "sources": {
                self.source_id: {
                    "source_id": self.source_id,
                    "canonical_url": "https://example.com/evidence",
                    "title": "Captured source",
                }
            },
            "task_source_refs": {"task_1": [{"task_id": "task_1", "source_id": self.source_id, "query": "offline", "attempt": 1}]},
            "report": "# Offline report\n\nA report generated from snapshots.",
        }


def test_offline_runner_writes_traceable_multi_run_artifacts_and_preserves_graph_state(tmp_path, monkeypatch):
    dataset = load_evaluation_dataset(_dataset_dir(tmp_path))
    original_factory = research.create_summarizer_agent
    monkeypatch.setattr(
        "src.evaluation.runner.create_summarizer_agent",
        lambda _llm, tools: tools,
    )
    runner = OfflineEvaluationRunner(
        dataset,
        runs=2,
        model_label="fake-model",
        route_label="fake-route",
        prompt_version="test-prompt",
        graph_factory=lambda: FakeSnapshotGraph("src_allowed", inspect_fixture=True),
    )
    output_dir = tmp_path / "evaluation-output"

    result = runner.run(output_dir)

    assert research.create_summarizer_agent is original_factory
    assert (output_dir / "config.json").is_file()
    assert (output_dir / "results.json").is_file()
    assert (output_dir / "summary.md").is_file()
    assert result["results"]["aggregate"]["case_run_count"] == 2
    assert result["results"]["aggregate"]["failed_case_run_count"] == 0
    assert result["config"]["route_label"] == "fake-route"
    assert result["config"]["routing"]["label"] == "fake-route"
    assert all(record["routing"]["label"] == "fake-route" for record in result["results"]["case_runs"])
    assert all(record["metrics"]["sources"]["source_scope_violation_count"] == 0 for record in result["results"]["case_runs"])
    assert all(record["fixture"]["call_count"] == 1 for record in result["results"]["case_runs"])
    assert "结构化引用覆盖率中位数" in (output_dir / "summary.md").read_text(encoding="utf-8")

    with pytest.raises(FileExistsError):
        runner.run(output_dir)


def test_offline_runner_marks_out_of_scope_graph_artifacts_as_failed(tmp_path):
    dataset = load_evaluation_dataset(_dataset_dir(tmp_path))
    runner = OfflineEvaluationRunner(
        dataset,
        runs=1,
        graph_factory=lambda: FakeSnapshotGraph("src_not_allowed"),
    )

    result = runner.run(tmp_path / "out-of-scope")
    case_run = result["results"]["case_runs"][0]

    assert case_run["status"] == "failed"
    assert case_run["metrics"]["sources"]["source_scope_violation_ids"] == ["src_not_allowed"]
    assert result["results"]["aggregate"]["source_scope_violation_count"] == 1
