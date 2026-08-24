"""Structured output parsing and validation for research agents."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Optional

from pydantic import ValidationError

from src.citations import validate_reference_structure
from src.state import (
    Claim,
    ParseStatus,
    SourceItem,
    TaskItem,
    TaskPlan,
    TaskResult,
    TaskStatus,
    new_id,
)


MAX_SUMMARY_LENGTH = 20_000
MIN_SUMMARY_LENGTH = 200


class StructuredOutputError(ValueError):
    """A model response could not satisfy the research artifact contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


Repairer = Callable[[str], str]


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract one JSON object from a direct or fenced model response."""
    if not isinstance(text, str) or not text.strip():
        raise StructuredOutputError("EMPTY_OUTPUT", "模型未返回可解析内容")

    fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidates = [*reversed(fenced_blocks), text]
    decoder = json.JSONDecoder()

    for candidate in candidates:
        candidate = candidate.strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        for match in re.finditer(r"\{", candidate):
            try:
                parsed, _ = decoder.raw_decode(candidate[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

    raise StructuredOutputError("INVALID_JSON", "模型输出中不存在合法 JSON 对象")


def _to_structured_error(exc: Exception, default_code: str) -> StructuredOutputError:
    if isinstance(exc, StructuredOutputError):
        return exc
    if isinstance(exc, ValidationError):
        return StructuredOutputError(default_code, exc.errors(include_url=False).__repr__())
    return StructuredOutputError(default_code, str(exc))


def parse_task_plan(text: str, topic: str, parse_status: ParseStatus = ParseStatus.VALID) -> TaskPlan:
    """Parse Planner JSON and assign stable task IDs and legacy display order."""
    try:
        payload = extract_json_object(text)
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            raise StructuredOutputError("PLAN_TASKS_MISSING", "任务规划缺少 tasks 数组")

        tasks = []
        for index, item in enumerate(raw_tasks, start=1):
            if not isinstance(item, dict):
                continue
            task_kwargs = {
                "task_id": str(item["task_id"]) if item.get("task_id") else new_id("task"),
                "id": item.get("id", index),
                "title": item.get("title", ""),
                "intent": item.get("intent", ""),
                "query": item.get("query", ""),
                "status": TaskStatus.PLANNED,
                "query_history": item.get("query_history") or [item.get("query", "")],
            }
            tasks.append(TaskItem(**task_kwargs))
        if len(tasks) != len(raw_tasks):
            raise StructuredOutputError("PLAN_TASK_INVALID", "tasks 数组中包含非对象元素")

        plan_kwargs = {
            "plan_version": payload.get("plan_version", 1),
            "topic": topic,
            "tasks": tasks,
            "parse_status": parse_status,
        }
        if payload.get("plan_id"):
            plan_kwargs["plan_id"] = str(payload["plan_id"])
        return TaskPlan(**plan_kwargs)
    except Exception as exc:
        raise _to_structured_error(exc, "PLAN_VALIDATION_FAILED") from exc


def parse_task_plan_with_repair(
    text: str,
    topic: str,
    repairer: Optional[Repairer] = None,
) -> TaskPlan:
    """Parse a plan once, then use at most one caller-provided format repair."""
    try:
        return parse_task_plan(text, topic)
    except StructuredOutputError as first_error:
        if repairer is None:
            raise first_error

        try:
            repaired = repairer(text)
            return parse_task_plan(repaired, topic, ParseStatus.REPAIRED)
        except Exception as repair_error:
            error = _to_structured_error(repair_error, "PLAN_REPAIR_FAILED")
            raise StructuredOutputError(
                "PLAN_REPAIR_FAILED",
                f"初次解析失败({first_error.code})，修复后仍无效：{error}",
            ) from repair_error


def _source_id(title: str, url: Optional[str]) -> str:
    """Create a provisional source identity until P0.2 content hashes are available."""
    digest = hashlib.sha256(f"{url or ''}|{title}".encode("utf-8")).hexdigest()[:20]
    return f"src_{digest}"


def parse_task_result(
    text: str,
    task: dict[str, Any],
    attempt: int,
    query: str,
    parse_status: ParseStatus = ParseStatus.VALID,
    available_sources: Optional[dict[str, SourceItem | dict[str, Any]]] = None,
) -> tuple[TaskResult, list[SourceItem]]:
    """Parse a P0.1 legacy or P0.2 claim-cited Summarizer response."""
    try:
        payload = extract_json_object(text)
        summary = payload.get("summary")
        if not isinstance(summary, str):
            raise StructuredOutputError("SUMMARY_MISSING", "任务摘要缺少 summary 字段")
        summary = summary.strip()
        if len(summary) < MIN_SUMMARY_LENGTH:
            raise StructuredOutputError("SUMMARY_TOO_SHORT", f"任务摘要不足 {MIN_SUMMARY_LENGTH} 字符")
        if len(summary) > MAX_SUMMARY_LENGTH:
            raise StructuredOutputError("SUMMARY_TOO_LONG", "任务摘要超过最大长度")
        if "暂无可用信息" in summary:
            raise StructuredOutputError("SUMMARY_NO_EVIDENCE", "任务摘要未包含有效证据")

        task_id = str(task.get("task_id") or "")
        if not task_id:
            raise StructuredOutputError("TASK_ID_MISSING", "任务没有 task_id")

        sources: list[SourceItem] = []
        claims: list[Claim] = []
        if available_sources is None:
            # Temporary P0.1 compatibility for callers that have not yet collected tool messages.
            raw_sources = payload.get("sources")
            if not isinstance(raw_sources, list) or not raw_sources:
                raise StructuredOutputError("SUMMARY_SOURCES_MISSING", "任务摘要缺少来源列表")
            for raw_source in raw_sources:
                if not isinstance(raw_source, dict):
                    raise StructuredOutputError("SOURCE_INVALID", "来源列表包含非对象元素")
                title = str(raw_source.get("title") or "无标题").strip()
                url = raw_source.get("url")
                if url is not None:
                    url = str(url).strip() or None
                if not url:
                    raise StructuredOutputError("SOURCE_URL_MISSING", "来源缺少 URL")
                sources.append(
                    SourceItem(
                        source_id=_source_id(title, url),
                        provider="agent_reported",
                        canonical_url=url,
                        title=title,
                    )
                )
            source_ids = [source.source_id for source in sources]
        else:
            if not available_sources:
                raise StructuredOutputError("TASK_SOURCES_MISSING", "本轮工具调用未返回可引用来源")
            raw_claims = payload.get("claims")
            if not isinstance(raw_claims, list) or not raw_claims:
                raise StructuredOutputError("CLAIMS_MISSING", "任务摘要缺少 claims 数组")
            for raw_claim in raw_claims:
                if not isinstance(raw_claim, dict):
                    raise StructuredOutputError("CLAIM_INVALID", "claims 数组中包含非对象元素")
                claims.append(
                    Claim(
                        claim_id=str(raw_claim["claim_id"]) if raw_claim.get("claim_id") else new_id("claim"),
                        task_id=task_id,
                        text=raw_claim.get("text", ""),
                        source_ids=raw_claim.get("source_ids", []),
                        evidence_status=raw_claim.get("evidence_status", "unverified"),
                    )
                )
            issues = validate_reference_structure(claims, task_id, set(available_sources))
            if issues:
                first_issue = issues[0]
                raise StructuredOutputError(first_issue["code"], first_issue["message"])
            source_ids = list(dict.fromkeys(source_id for claim in claims for source_id in claim.source_ids))

        result = TaskResult(
            task_id=task_id,
            status=TaskStatus.SUCCEEDED,
            attempts=attempt,
            query_history=task.get("query_history") or [query],
            summary=summary,
            source_ids=source_ids,
            claims=claims,
            parse_status=parse_status,
        )
        return result, sources
    except Exception as exc:
        raise _to_structured_error(exc, "SUMMARY_VALIDATION_FAILED") from exc


def parse_task_result_with_repair(
    text: str,
    task: dict[str, Any],
    attempt: int,
    query: str,
    repairer: Optional[Repairer] = None,
    available_sources: Optional[dict[str, SourceItem | dict[str, Any]]] = None,
) -> tuple[TaskResult, list[SourceItem]]:
    """Parse a task result once, then use at most one format-only repair."""
    try:
        return parse_task_result(text, task, attempt, query, available_sources=available_sources)
    except StructuredOutputError as first_error:
        if repairer is None:
            raise first_error

        try:
            repaired = repairer(text)
            return parse_task_result(
                repaired,
                task,
                attempt,
                query,
                ParseStatus.REPAIRED,
                available_sources,
            )
        except Exception as repair_error:
            error = _to_structured_error(repair_error, "SUMMARY_REPAIR_FAILED")
            raise StructuredOutputError(
                "SUMMARY_REPAIR_FAILED",
                f"初次解析失败({first_error.code})，修复后仍无效：{error}",
            ) from repair_error


def failed_task_result(
    task: dict[str, Any],
    attempts: int,
    error_code: str,
    error_message: str,
    query_history: list[str],
) -> TaskResult:
    """Create an explicit failure artifact without fabricating research content."""
    task_id = str(task.get("task_id") or "unknown_task")
    safe_message = error_message.strip().replace("\x00", " ")[:1_000]
    return TaskResult(
        task_id=task_id,
        status=TaskStatus.FAILED,
        attempts=max(attempts, 1),
        query_history=query_history,
        summary=f"任务未完成：{safe_message or '未获得可验证结果。'}",
        error_code=error_code,
        error_message=safe_message or None,
        parse_status=ParseStatus.REJECTED,
    )
