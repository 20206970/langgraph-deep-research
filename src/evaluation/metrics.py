"""Deterministic metrics for snapshot-grounded evaluation runs."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Any

from src.citations import build_citation_report


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _facet_terms(facet: str) -> list[str]:
    """Split explicitly listed alternatives without pretending to understand semantics."""
    terms = [term.strip() for term in re.split(r"[、，,;；/／和与及或]", facet) if len(term.strip()) >= 2]
    return terms or [facet.strip()]


def _plan_metrics(case: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    tasks = _as_list(plan.get("tasks"))
    task_dicts = [task for task in tasks if isinstance(task, dict)]
    fields = ("title", "intent", "query")
    total_fields = len(task_dicts) * len(fields)
    completed_fields = sum(
        bool(str(task.get(field) or "").strip()) for task in task_dicts for field in fields
    )
    queries = [_normalized_text(task.get("query")) for task in task_dicts if _normalized_text(task.get("query"))]
    target_range = _as_dict(case.get("target_task_count"))
    minimum = target_range.get("min")
    maximum = target_range.get("max")
    task_count_in_target_range = (
        isinstance(minimum, int)
        and isinstance(maximum, int)
        and minimum <= len(task_dicts) <= maximum
    )
    planning_text = _normalized_text(
        " ".join(str(task.get(field) or "") for task in task_dicts for field in fields)
    )
    facet_matches = []
    for facet in _as_list(case.get("expected_facets")):
        facet_text = str(facet)
        matched_terms = [term for term in _facet_terms(facet_text) if _normalized_text(term) in planning_text]
        facet_matches.append({"facet": facet_text, "matched_terms": matched_terms, "matched": bool(matched_terms)})

    return {
        "parse_status": plan.get("parse_status") or "missing",
        "valid": plan.get("parse_status") == "valid",
        "task_count": len(task_dicts),
        "target_task_count": {"min": minimum, "max": maximum},
        "task_count_in_target_range": task_count_in_target_range,
        "task_field_completeness": completed_fields / total_fields if total_fields else 0.0,
        "query_duplicate_rate": (len(queries) - len(set(queries))) / len(queries) if queries else 0.0,
        "facet_coverage_proxy": (
            sum(item["matched"] for item in facet_matches) / len(facet_matches) if facet_matches else None
        ),
        "facet_matches": facet_matches,
        "facet_proxy_limitation": "仅基于规划文本的显式词面匹配，不等同于语义覆盖或研究质量。",
    }


def _task_metrics(task_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    results = list(task_results.values())
    statuses = [str(result.get("status") or "missing") for result in results]
    attempts = [result.get("attempts") for result in results if isinstance(result.get("attempts"), int)]
    source_empty = sum(not _as_list(result.get("source_ids")) for result in results)
    latency_values = [result.get("latency_ms") for result in results if isinstance(result.get("latency_ms"), int)]
    token_totals: Counter[str] = Counter()
    token_available_task_count = 0
    cache_hit_task_count = 0
    estimated_costs: list[float] = []
    unavailable_cost_task_count = 0
    budget_statuses: Counter[str] = Counter()
    for result in results:
        token_usage = _as_dict(result.get("token_usage"))
        numeric_usage = {key: value for key, value in token_usage.items() if isinstance(value, int) and value >= 0}
        if numeric_usage:
            token_available_task_count += 1
            token_totals.update(numeric_usage)
        if result.get("cache_hit") is True:
            cache_hit_task_count += 1
        cost = result.get("estimated_cost")
        if result.get("cost_status") == "estimated" and isinstance(cost, (int, float)):
            estimated_costs.append(float(cost))
        else:
            unavailable_cost_task_count += 1
        budget_statuses[str(result.get("budget_status") or "unknown")] += 1
    total = len(results)
    return {
        "total_count": total,
        "succeeded_count": statuses.count("succeeded"),
        "failed_count": total - statuses.count("succeeded"),
        "failure_rate": (total - statuses.count("succeeded")) / total if total else 1.0,
        "source_empty_count": source_empty,
        "source_empty_rate": source_empty / total if total else 1.0,
        "retry_task_count": sum(attempt > 1 for attempt in attempts),
        "retry_task_rate": sum(attempt > 1 for attempt in attempts) / total if total else 0.0,
        "attempt_count": sum(attempts),
        "latency_ms": {
            "available_task_count": len(latency_values),
            "total": sum(latency_values) if latency_values else None,
        },
        "token_usage": {
            "available_task_count": token_available_task_count,
            "total": dict(sorted(token_totals.items())) if token_available_task_count else None,
        },
        "cache": {
            "cache_hit_task_count": cache_hit_task_count,
            "cache_hit_rate": cache_hit_task_count / total if total else None,
        },
        "estimated_cost": {
            "available_task_count": len(estimated_costs),
            "unavailable_task_count": unavailable_cost_task_count,
            "total": round(sum(estimated_costs), 12) if estimated_costs and not unavailable_cost_task_count else None,
        },
        "budget_statuses": dict(sorted(budget_statuses.items())),
    }


def _all_referenced_source_ids(task_results: dict[str, dict[str, Any]], task_source_refs: dict[str, list[dict[str, Any]]]) -> set[str]:
    source_ids: set[str] = set()
    for result in task_results.values():
        source_ids.update(source_id for source_id in _as_list(result.get("source_ids")) if isinstance(source_id, str))
        for claim in _as_list(result.get("claims")):
            if isinstance(claim, dict):
                source_ids.update(source_id for source_id in _as_list(claim.get("source_ids")) if isinstance(source_id, str))
    for refs in task_source_refs.values():
        for ref in _as_list(refs):
            if isinstance(ref, dict) and isinstance(ref.get("source_id"), str):
                source_ids.add(ref["source_id"])
    return source_ids


def _source_metrics(
    case: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    task_results: dict[str, dict[str, Any]],
    task_source_refs: dict[str, list[dict[str, Any]]],
    fixture_audit: dict[str, Any],
) -> dict[str, Any]:
    allowed_source_ids = set(_as_list(case.get("allowed_source_ids")))
    referenced_source_ids = _all_referenced_source_ids(task_results, task_source_refs)
    emitted_source_ids = set(sources)
    all_observed_source_ids = referenced_source_ids | emitted_source_ids
    out_of_scope = sorted(all_observed_source_ids - allowed_source_ids)
    existing_references = referenced_source_ids & emitted_source_ids
    return {
        "allowed_source_count": len(allowed_source_ids),
        "emitted_source_count": len(emitted_source_ids),
        "referenced_source_count": len(referenced_source_ids),
        "source_existence_rate": (
            len(existing_references) / len(referenced_source_ids) if referenced_source_ids else None
        ),
        "source_scope_violation_count": len(out_of_scope),
        "source_scope_violation_ids": out_of_scope,
        "fixture_call_count": fixture_audit.get("call_count", 0),
        "fixture_emitted_source_ids": _as_list(fixture_audit.get("emitted_source_ids")),
        "fixture_scope_violation_count": fixture_audit.get("emitted_source_scope_violation_count", 0),
    }


def _annotation_metrics(annotations: list[dict[str, Any]], task_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cited_source_ids = set()
    for result in task_results.values():
        for claim in _as_list(result.get("claims")):
            if isinstance(claim, dict):
                cited_source_ids.update(source_id for source_id in _as_list(claim.get("source_ids")) if isinstance(source_id, str))
    labels = Counter(str(annotation.get("label") or "missing") for annotation in annotations)
    supported = [annotation for annotation in annotations if annotation.get("label") == "supported"]
    matched_supported = sum(bool(set(_as_list(annotation.get("source_ids"))) & cited_source_ids) for annotation in supported)
    return {
        "annotation_count": len(annotations),
        "label_counts": dict(sorted(labels.items())),
        "supported_annotation_cited_source_recall_proxy": (
            matched_supported / len(supported) if supported else None
        ),
        "semantic_evidence_support_rate": None,
        "limitation": "标注与模型输出未做逐条结论对齐；该 proxy 只统计人工 supported 标注来源是否被模型引用，不能作为证据蕴含评分。",
    }


def compute_case_metrics(
    case: dict[str, Any],
    graph_result: dict[str, Any],
    fixture_audit: dict[str, Any],
    annotations: list[dict[str, Any]],
    elapsed_ms: int,
) -> dict[str, Any]:
    """Compute transparent metrics without semantic judging or live URL checks."""
    plan = _as_dict(graph_result.get("plan"))
    task_results = {
        str(task_id): _as_dict(result)
        for task_id, result in _as_dict(graph_result.get("task_results")).items()
    }
    sources = {
        str(source_id): _as_dict(source)
        for source_id, source in _as_dict(graph_result.get("sources")).items()
    }
    task_source_refs = {
        str(task_id): [ref for ref in _as_list(refs) if isinstance(ref, dict)]
        for task_id, refs in _as_dict(graph_result.get("task_source_refs")).items()
    }
    citation = build_citation_report(task_results, sources, task_source_refs)
    claim_count = citation["summary"]["claim_count"]
    issue_claim_ids = {
        issue.get("claim_id")
        for task in citation["tasks"]
        for issue in task["issues"]
        if issue.get("claim_id")
    }
    issue_claim_ids.update(issue.get("claim_id") for issue in citation["cross_task_issues"] if issue.get("claim_id"))
    report = str(graph_result.get("report") or "").strip()
    return {
        "planner": _plan_metrics(case, plan),
        "tasks": _task_metrics(task_results),
        "sources": _source_metrics(case, sources, task_results, task_source_refs, fixture_audit),
        "citation": {
            **citation,
            "structural_valid_claim_rate": (claim_count - len(issue_claim_ids)) / claim_count if claim_count else None,
        },
        "report": {"non_empty": bool(report), "character_count": len(report)},
        "annotations": _annotation_metrics(annotations, task_results),
        "total_elapsed_ms": elapsed_ms,
    }


def _summary_stats(values: list[float | int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "median": None, "min": None, "max": None}
    return {
        "count": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def aggregate_case_runs(case_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only values that can be traced back to individual case-run records."""
    metrics = [record["metrics"] for record in case_runs if isinstance(record.get("metrics"), dict)]
    successful = [record for record in case_runs if record.get("status") == "succeeded"]
    total = len(case_runs)
    elapsed_values = [metric["total_elapsed_ms"] for metric in metrics if isinstance(metric.get("total_elapsed_ms"), int)]
    task_counts = [metric["tasks"]["total_count"] for metric in metrics]
    failed_task_counts = [metric["tasks"]["failed_count"] for metric in metrics]
    total_tasks = sum(task_counts)
    planner_valid = [metric["planner"]["valid"] for metric in metrics]
    facet_values = [metric["planner"]["facet_coverage_proxy"] for metric in metrics if metric["planner"]["facet_coverage_proxy"] is not None]
    citation_values = [metric["citation"]["summary"]["citation_coverage"] for metric in metrics]
    source_violation_count = sum(metric["sources"]["source_scope_violation_count"] for metric in metrics)
    cache_hit_task_count = sum(metric["tasks"]["cache"]["cache_hit_task_count"] for metric in metrics)
    estimated_cost_values = [
        metric["tasks"]["estimated_cost"]["total"]
        for metric in metrics
        if metric["tasks"]["estimated_cost"]["total"] is not None
    ]
    return {
        "case_run_count": total,
        "succeeded_case_run_count": len(successful),
        "failed_case_run_count": total - len(successful),
        "failure_rate": (total - len(successful)) / total if total else None,
        "total_elapsed_ms": _summary_stats(elapsed_values),
        "task_failure_rate": sum(failed_task_counts) / total_tasks if total_tasks else None,
        "planner_valid_rate": sum(planner_valid) / len(planner_valid) if planner_valid else None,
        "facet_coverage_proxy": _summary_stats(facet_values),
        "citation_coverage": _summary_stats(citation_values),
        "source_scope_violation_count": source_violation_count,
        "cache_hit_task_count": cache_hit_task_count,
        "cache_hit_rate": cache_hit_task_count / total_tasks if total_tasks else None,
        "estimated_cost": _summary_stats(estimated_cost_values),
    }
