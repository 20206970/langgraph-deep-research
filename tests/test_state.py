import json

import pytest

from src.state import (
    ParseStatus,
    SourceItem,
    TaskItem,
    TaskPlan,
    merge_sources,
    merge_output_diagnostics,
    merge_task_results,
    merge_task_source_refs,
)


def test_task_plan_json_round_trip_preserves_stable_task_ids():
    plan = TaskPlan(
        topic="LangGraph research",
        tasks=[TaskItem(id=1, title="Architecture", intent="Review graph design", query="LangGraph architecture")],
    )

    restored = TaskPlan.model_validate_json(json.dumps(plan.model_dump(mode="json")))

    assert restored.schema_version == 1
    assert restored.tasks[0].task_id == plan.tasks[0].task_id
    assert restored.tasks[0].query_history == ["LangGraph architecture"]


def test_full_state_payload_is_json_round_trip_safe():
    plan = TaskPlan(
        topic="topic",
        tasks=[TaskItem(id=1, title="Task", intent="intent", query="query")],
    )
    payload = {
        "plan": plan.model_dump(mode="json"),
        "task_results": {plan.tasks[0].task_id: {"task_id": plan.tasks[0].task_id, "status": "succeeded"}},
        "sources": {"src_1": {"source_id": "src_1", "canonical_url": "https://example.com"}},
        "output_diagnostics": {"planner": {"sha256": "abc", "length": 3, "parse_status": "valid"}},
    }

    assert json.loads(json.dumps(payload)) == payload


def test_duplicate_queries_are_rejected_even_when_case_differs():
    with pytest.raises(ValueError, match="task queries must be unique"):
        TaskPlan(
            topic="topic",
            tasks=[
                TaskItem(id=1, title="A", intent="first", query="LangGraph State"),
                TaskItem(id=2, title="B", intent="second", query=" langgraph   state "),
            ],
        )


def test_rejected_plan_can_be_serialized_without_tasks():
    plan = TaskPlan(topic="topic", tasks=[], parse_status=ParseStatus.REJECTED, error_code="INVALID_JSON")

    assert plan.model_dump(mode="json")["parse_status"] == "rejected"


def test_task_result_reducer_rejects_conflicting_parallel_writes():
    with pytest.raises(ValueError, match="conflicting task result"):
        merge_task_results(
            {"task_1": {"summary": "first"}},
            {"task_1": {"summary": "second"}},
        )


def test_task_result_reducer_accepts_a_strictly_newer_retry_attempt():
    merged = merge_task_results(
        {"task_1": {"task_id": "task_1", "attempts": 1, "status": "failed"}},
        {"task_1": {"task_id": "task_1", "attempts": 2, "status": "succeeded"}},
    )

    assert merged["task_1"]["attempts"] == 2
    assert merged["task_1"]["status"] == "succeeded"


def test_source_reducer_keeps_richer_evidence_snapshot():
    merged = merge_sources(
        {"src_1": {"source_id": "src_1", "title": "Doc", "canonical_url": "https://example.com"}},
        {
            "src_1": SourceItem(
                source_id="src_1",
                title="Doc",
                canonical_url="https://example.com",
                content_hash="hash",
                evidence_excerpt="A useful evidence excerpt.",
            ).model_dump(mode="json")
        },
    )

    assert merged["src_1"]["content_hash"] == "hash"
    assert merged["src_1"]["evidence_excerpt"] == "A useful evidence excerpt."


def test_task_source_ref_reducer_deduplicates_source_per_task():
    merged = merge_task_source_refs(
        {"task_1": [{"task_id": "task_1", "source_id": "src_1", "query": "first", "attempt": 1}]},
        {
            "task_1": [
                {"task_id": "task_1", "source_id": "src_1", "query": "retry", "attempt": 2},
                {"task_id": "task_1", "source_id": "src_2", "query": "retry", "attempt": 2},
            ]
        },
    )

    assert [ref["source_id"] for ref in merged["task_1"]] == ["src_1", "src_2"]


def test_output_diagnostic_reducer_rejects_conflicting_metadata():
    with pytest.raises(ValueError, match="conflicting output diagnostic"):
        merge_output_diagnostics(
            {"planner": {"sha256": "first", "length": 1}},
            {"planner": {"sha256": "second", "length": 1}},
        )
