import json

import pytest

from src.state import ParseStatus
from src.validation import (
    StructuredOutputError,
    parse_task_plan,
    parse_task_plan_with_repair,
    parse_task_result,
    parse_task_result_with_repair,
)


def _valid_plan() -> str:
    return json.dumps(
        {
            "tasks": [
                {"title": "Architecture", "intent": "Review graph design", "query": "LangGraph architecture"},
                {"title": "Testing", "intent": "Review testing", "query": "LangGraph tests"},
            ]
        },
        ensure_ascii=False,
    )


def _valid_summary() -> str:
    return "已检索到可追溯资料，并从架构、执行流程、失败边界和工程实践四个方面进行了归纳。" * 8


def test_plan_parser_accepts_direct_and_fenced_json():
    direct = parse_task_plan(_valid_plan(), "topic")
    fenced = parse_task_plan(f"```json\n{_valid_plan()}\n```", "topic")

    assert len(direct.tasks) == 2
    assert direct.tasks[0].task_id.startswith("task_")
    assert fenced.tasks[1].id == 2


def test_plan_parser_repairs_one_invalid_response():
    repaired = parse_task_plan_with_repair("not json", "topic", repairer=lambda _: _valid_plan())

    assert repaired.parse_status == ParseStatus.REPAIRED
    assert len(repaired.tasks) == 2


def test_plan_parser_reports_repair_failure():
    with pytest.raises(StructuredOutputError) as error:
        parse_task_plan_with_repair("not json", "topic", repairer=lambda _: "still not json")

    assert error.value.code == "PLAN_REPAIR_FAILED"


def test_plan_parser_rejects_duplicate_queries():
    payload = {
        "tasks": [
            {"title": "A", "intent": "first", "query": "same query"},
            {"title": "B", "intent": "second", "query": " SAME   QUERY "},
        ]
    }

    with pytest.raises(StructuredOutputError) as error:
        parse_task_plan(json.dumps(payload), "topic")

    assert error.value.code == "PLAN_VALIDATION_FAILED"


@pytest.mark.parametrize(
    "payload",
    [
        {"tasks": [{"title": "A", "intent": "first"}]},
        {"tasks": [{"title": "A" * 201, "intent": "first", "query": "query"}]},
        {
            "tasks": [
                {"title": f"Task {index}", "intent": "intent", "query": f"query {index}"}
                for index in range(8)
            ]
        },
    ],
)
def test_plan_parser_rejects_missing_or_overlong_task_fields(payload):
    with pytest.raises(StructuredOutputError) as error:
        parse_task_plan(json.dumps(payload, ensure_ascii=False), "topic")

    assert error.value.code == "PLAN_VALIDATION_FAILED"


def test_task_result_parser_accepts_sources_and_builds_source_ids():
    task = {"task_id": "task_1", "query_history": ["LangGraph architecture"]}
    payload = json.dumps(
        {
            "summary": _valid_summary(),
            "sources": [{"title": "LangGraph docs", "url": "https://langchain-ai.github.io/langgraph/"}],
        },
        ensure_ascii=False,
    )

    result, sources = parse_task_result(payload, task, 1, "LangGraph architecture")

    assert result.status.value == "succeeded"
    assert result.source_ids == [sources[0].source_id]
    assert sources[0].canonical_url.startswith("https://")


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"summary": "too short", "sources": [{"title": "Doc", "url": "https://example.com"}]}, "SUMMARY_TOO_SHORT"),
        ({"summary": _valid_summary(), "sources": [{"title": "Doc"}]}, "SOURCE_URL_MISSING"),
    ],
)
def test_task_result_parser_rejects_invalid_evidence(payload, code):
    with pytest.raises(StructuredOutputError) as error:
        parse_task_result(json.dumps(payload, ensure_ascii=False), {"task_id": "task_1"}, 1, "query")

    assert error.value.code == code


def test_task_result_parser_repairs_one_invalid_response():
    valid = json.dumps(
        {
            "summary": _valid_summary(),
            "sources": [{"title": "Doc", "url": "https://example.com"}],
        },
        ensure_ascii=False,
    )

    result, _ = parse_task_result_with_repair(
        "broken",
        {"task_id": "task_1"},
        1,
        "query",
        repairer=lambda _: valid,
    )

    assert result.parse_status == ParseStatus.REPAIRED


def test_task_result_parser_validates_claim_source_ids_against_tool_sources():
    payload = json.dumps(
        {
            "summary": _valid_summary(),
            "claims": [{"text": "A supported claim", "source_ids": ["src_1"], "evidence_status": "supported"}],
        },
        ensure_ascii=False,
    )

    result, sources = parse_task_result(
        payload,
        {"task_id": "task_1"},
        1,
        "query",
        available_sources={"src_1": {"source_id": "src_1"}},
    )

    assert sources == []
    assert result.claims[0].source_ids == ["src_1"]


def test_task_result_parser_rejects_claims_that_reference_other_task_sources():
    payload = json.dumps(
        {
            "summary": _valid_summary(),
            "claims": [{"text": "An unsupported claim", "source_ids": ["src_other"], "evidence_status": "supported"}],
        },
        ensure_ascii=False,
    )

    with pytest.raises(StructuredOutputError) as error:
        parse_task_result(
            payload,
            {"task_id": "task_1"},
            1,
            "query",
            available_sources={"src_1": {"source_id": "src_1"}},
        )

    assert error.value.code == "CLAIM_SOURCE_NOT_AVAILABLE"
