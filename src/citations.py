"""Citation structure checks for versioned research artifacts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any


def _value(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _as_source_ids(claim: Any) -> list[str] | None:
    source_ids = _value(claim, "source_ids", [])
    if not isinstance(source_ids, list) or not all(isinstance(source_id, str) for source_id in source_ids):
        return None
    return source_ids


def validate_reference_structure(
    claims: Iterable[Any],
    task_id: str,
    allowed_source_ids: set[str],
) -> list[dict[str, str]]:
    """Return task-local citation errors without inferring factual correctness."""
    issues: list[dict[str, str]] = []
    for index, claim in enumerate(claims, start=1):
        claim_id = str(_value(claim, "claim_id", f"claim_{index}"))
        claim_task_id = str(_value(claim, "task_id", task_id))
        source_ids = _as_source_ids(claim)
        evidence_status = str(_value(claim, "evidence_status", "unverified"))

        if claim_task_id != task_id:
            issues.append({"claim_id": claim_id, "code": "CLAIM_TASK_MISMATCH", "message": "结论不属于当前任务"})
            continue
        if source_ids is None:
            issues.append({"claim_id": claim_id, "code": "CLAIM_SOURCE_IDS_INVALID", "message": "source_ids 必须是字符串数组"})
            continue
        if len(source_ids) != len(set(source_ids)):
            issues.append({"claim_id": claim_id, "code": "CLAIM_SOURCE_IDS_DUPLICATE", "message": "结论包含重复来源"})
        if evidence_status != "insufficient" and not source_ids:
            issues.append({"claim_id": claim_id, "code": "CLAIM_SOURCE_IDS_MISSING", "message": "非证据不足结论必须引用来源"})
        for source_id in source_ids:
            if source_id not in allowed_source_ids:
                issues.append(
                    {
                        "claim_id": claim_id,
                        "code": "CLAIM_SOURCE_NOT_AVAILABLE",
                        "message": f"来源 {source_id} 不属于当前任务",
                    }
                )
    return issues


def validate_source_accessibility(
    sources: Iterable[Any],
    fetcher: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Check URLs with an optional caller-provided fetcher; no implicit network calls."""
    results = []
    for source in sources:
        source_id = str(_value(source, "source_id", ""))
        url = _value(source, "canonical_url", None) or _value(source, "url", None)
        if not url:
            results.append({"source_id": source_id, "accessible": False, "status": "missing_url"})
        elif fetcher is None:
            results.append({"source_id": source_id, "accessible": None, "status": "not_checked"})
        else:
            try:
                results.append({"source_id": source_id, "accessible": bool(fetcher(str(url))), "status": "checked"})
            except Exception:
                results.append({"source_id": source_id, "accessible": False, "status": "check_failed"})
    return results


def calculate_citation_coverage(claims: Iterable[Any]) -> float:
    """Calculate the share of claims carrying at least one source reference."""
    claims = list(claims)
    if not claims:
        return 0.0
    cited = sum(bool(_as_source_ids(claim)) for claim in claims)
    return cited / len(claims)


def find_cross_task_references(
    claims: Iterable[Any],
    task_source_refs: dict[str, list[dict[str, Any]]],
) -> list[dict[str, str]]:
    """Find source IDs cited by a task but associated with another task."""
    owners: dict[str, set[str]] = defaultdict(set)
    for owner_task_id, refs in task_source_refs.items():
        for ref in refs:
            source_id = str(ref.get("source_id") or "")
            if source_id:
                owners[source_id].add(owner_task_id)

    issues = []
    for index, claim in enumerate(claims, start=1):
        claim_id = str(_value(claim, "claim_id", f"claim_{index}"))
        task_id = str(_value(claim, "task_id", ""))
        for source_id in _as_source_ids(claim) or []:
            if source_id in owners and task_id not in owners[source_id]:
                issues.append(
                    {
                        "claim_id": claim_id,
                        "source_id": source_id,
                        "code": "CROSS_TASK_REFERENCE",
                        "message": "结论引用了其他任务的来源",
                    }
                )
    return issues


def build_citation_report(
    task_results: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    task_source_refs: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build task-level structural citation metrics for logs and offline evaluation."""
    task_reports = []
    all_claims = []
    all_issues = []
    for task_id, result in task_results.items():
        claims = result.get("claims", [])
        allowed_source_ids = {ref.get("source_id") for ref in task_source_refs.get(task_id, []) if ref.get("source_id")}
        issues = validate_reference_structure(claims, task_id, allowed_source_ids)
        task_reports.append(
            {
                "task_id": task_id,
                "claim_count": len(claims),
                "citation_coverage": calculate_citation_coverage(claims),
                "issues": issues,
            }
        )
        all_claims.extend(claims)
        all_issues.extend(issues)

    cross_task_issues = find_cross_task_references(all_claims, task_source_refs)
    all_issues.extend(cross_task_issues)
    source_status = validate_source_accessibility(sources.values())
    return {
        "tasks": task_reports,
        "summary": {
            "claim_count": len(all_claims),
            "citation_coverage": calculate_citation_coverage(all_claims),
            "issue_count": len(all_issues),
            "source_count": len(sources),
            "sources_with_url": sum(bool(item.get("canonical_url")) for item in sources.values()),
        },
        "cross_task_issues": cross_task_issues,
        "source_accessibility": source_status,
    }
