"""Research workflow with versioned artifacts and deterministic aggregation."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.agents import create_planner_agent, create_reporter_agent, create_summarizer_agent
from src.budget import aggregate_budget_usage, budget_exceeded, budget_from_config, with_budget_status
from src.costs import estimate_cost, extract_token_usage
from src.events import EventType, emit_event
from src.llm import model_versions
from src.memory.long_term import get_long_term_memory, save_research_memory, search_long_term_memory
from src.memory.short_term import get_memory_context, get_short_term_memory
from src.state import (
    ParseStatus,
    ReportArtifact,
    ResearchRun,
    RunBudget,
    RunStatus,
    SourceItem,
    TaskPlan,
    TaskResult,
    TaskSourceRef,
    TaskStatus,
    utc_now,
)
from src.validation import (
    StructuredOutputError,
    failed_task_result,
    parse_task_plan_with_repair,
    parse_task_result,
)


def _create_llm(role: str = "default"):
    """Create an LLM instance from the current configuration."""
    from src.llm import create_llm

    return create_llm(role=role)


def _response_messages(response: Any) -> list[Any]:
    if isinstance(response, dict):
        return list(response.get("messages", []))
    if hasattr(response, "get"):
        return list(response.get("messages", []))
    return []


def _message_content(response: Any) -> str:
    """Read final text from an agent result, AI message, or test double."""
    messages = _response_messages(response)
    if messages:
        response = messages[-1]

    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
        return "\n".join(text_parts)
    return str(content or "")


def _message_field(message: Any, name: str, default: Any = None) -> Any:
    return message.get(name, default) if isinstance(message, dict) else getattr(message, name, default)


def _collect_tool_sources(response: Any) -> dict[str, SourceItem]:
    """Read P0.2 normalized sources only from this ReAct invocation's tool messages."""
    sources: dict[str, SourceItem] = {}
    for message in _response_messages(response):
        message_type = _message_field(message, "type", "")
        tool_name = _message_field(message, "name", "")
        if message_type != "tool" or tool_name not in {"search_web", "search_papers"}:
            continue
        try:
            payload = json.loads(_message_content(message))
        except (TypeError, json.JSONDecodeError):
            continue
        for raw_source in payload.get("results", []):
            if not isinstance(raw_source, dict) or not raw_source.get("source_id"):
                continue
            try:
                source = SourceItem(
                    source_id=str(raw_source["source_id"]),
                    source_type=str(raw_source.get("source_type") or "web"),
                    provider=str(raw_source.get("provider") or payload.get("provider") or "unknown"),
                    canonical_url=raw_source.get("canonical_url") or raw_source.get("url"),
                    title=raw_source.get("title"),
                    retrieved_at=raw_source.get("retrieved_at") or utc_now(),
                    content_hash=raw_source.get("content_hash"),
                    evidence_excerpt=raw_source.get("evidence_excerpt") or raw_source.get("content"),
                    locator=raw_source.get("locator"),
                )
            except Exception:
                continue
            sources[source.source_id] = source
    return sources


def _collect_tool_metadata(response: Any) -> dict[str, Any]:
    """Read bounded cache/provider metadata exposed by this invocation's tool messages."""
    cache_hit = False
    providers: set[str] = set()
    for message in _response_messages(response):
        if _message_field(message, "type", "") != "tool":
            continue
        try:
            payload = json.loads(_message_content(message))
        except (TypeError, json.JSONDecodeError):
            continue
        cache_hit = cache_hit or bool(payload.get("cache_hit"))
        provider = payload.get("provider")
        if isinstance(provider, str) and provider:
            providers.add(provider)
    return {"cache_hit": cache_hit, "providers": sorted(providers)}


def _strip_reasoning(text: str) -> str:
    """Remove visible reasoning blocks before parsing or returning model text."""
    return text.split("</think>", 1)[-1].strip() if "</think>" in text else text.strip()


def _safe_error_message(error: Exception | str) -> str:
    """Keep short, redacted error summaries in JSON state."""
    message = str(error).replace("\x00", " ")
    message = re.sub(
        r"(?i)(api[_ -]?key|authorization|bearer)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        message,
    )
    return message[:500]


def _output_diagnostic(
    raw_output: str,
    parse_status: ParseStatus,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Persist only output metadata required for debugging structured contracts."""
    return {
        "sha256": hashlib.sha256(raw_output.encode("utf-8")).hexdigest(),
        "length": len(raw_output),
        "parse_status": parse_status.value,
        "parse_error_code": error_code,
    }


def _format_repair_response(llm: Any, schema: str, raw_output: str) -> tuple[str, Any]:
    """Run one format-only repair while preserving the provider response for accounting."""
    response = llm.invoke(
        [
            (
                "system",
                "你只负责修复 JSON 结构。不得搜索、不得新增事实、不得解释。"
                "将用户提供的内容改写为符合指定 schema 的单个 JSON 对象。",
            ),
            ("user", f"目标 schema：\n{schema}\n\n待修复内容：\n{raw_output}"),
        ]
    )
    return _strip_reasoning(_message_content(response)), response


def _format_repairer(llm: Any, schema: str) -> Callable[[str], str]:
    """Return a format-only repair callback for validation helpers."""

    def repair(raw_output: str) -> str:
        repaired, _ = _format_repair_response(llm, schema, raw_output)
        return repaired

    return repair


def _plan_repairer(llm: Any) -> Callable[[str], str]:
    return _format_repairer(
        llm,
        '{"tasks":[{"title":"任务名称","intent":"任务目标","query":"检索查询"}]}',
    )


def _result_repair(llm: Any, raw_output: str, available_source_ids: set[str]) -> tuple[str, Any]:
    source_ids = ", ".join(sorted(available_source_ids)) or "(无)"
    schema = (
        '{"summary":"至少 200 字的事实性总结","claims":[{"text":"可验证结论",'
        '"source_ids":["src_xxx"],"evidence_status":"supported"}]}。'
        f"允许的 source_id：{source_ids}"
    )
    return _format_repair_response(llm, schema, raw_output)


def _build_rejected_plan(topic: str, error: StructuredOutputError) -> TaskPlan:
    return TaskPlan(
        topic=topic,
        tasks=[],
        parse_status=ParseStatus.REJECTED,
        error_code=error.code,
        error_message=_safe_error_message(error),
    )


def _new_run(
    topic: str,
    session_id: str | None,
    plan: TaskPlan,
    status: RunStatus,
    llm: Any = None,
    output_diagnostics: dict[str, dict[str, Any]] | None = None,
) -> ResearchRun:
    from src.config import get_config

    return ResearchRun(
        thread_id=session_id or "",
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        topic=topic,
        status=status,
        model_versions=model_versions(),
        prompt_versions={"planner": "p0.1", "summarizer": "p0.2", "reporter": "p0.2"},
        budget=budget_from_config(get_config()),
        output_diagnostics=output_diagnostics or {},
    )


def _run_budget(state: dict[str, Any]) -> RunBudget:
    """Read a persisted budget while keeping direct graph tests backward compatible."""
    try:
        return ResearchRun.model_validate(state.get("run") or {}).budget
    except Exception:
        from src.config import get_config

        return budget_from_config(get_config())


def _budget_usage(state: dict[str, Any]):
    run = state.get("run") or {}
    usage = aggregate_budget_usage(
        dict(state.get("task_results") or {}),
        created_at=run.get("created_at"),
    )
    return with_budget_status(_run_budget(state), usage)


def _budget_usage_with_current_task(
    state: dict[str, Any],
    task_id: str,
    attempts: int,
    token_usage: dict[str, int],
    estimated_cost: float | None,
    cost_status: str,
):
    """Include in-flight task usage in a cooperative budget checkpoint."""
    results = dict(state.get("task_results") or {})
    results[task_id] = {
        "task_id": task_id,
        "attempts": max(attempts, 1),
        "token_usage": token_usage,
        "estimated_cost": estimated_cost,
        "cost_status": cost_status,
    }
    run = state.get("run") or {}
    usage = aggregate_budget_usage(results, created_at=run.get("created_at"))
    return with_budget_status(_run_budget(state), usage)


def _merge_token_usage(total: dict[str, int], additional: dict[str, int]) -> dict[str, int]:
    """Accumulate provider usage across retries without depending on one SDK shape."""
    merged = dict(total)
    for key, value in additional.items():
        if isinstance(value, int) and value >= 0:
            merged[key] = int(merged.get(key) or 0) + value
    if "total_tokens" not in merged and {"input_tokens", "output_tokens"} <= merged.keys():
        merged["total_tokens"] = merged["input_tokens"] + merged["output_tokens"]
    return merged


def _budget_scope_reasons(results: dict[str, dict[str, Any]], usage: Any) -> list[str]:
    """Return stable, human-readable budget reasons for final report scope notes."""
    reasons = {
        str(result.get("error_message") or "运行预算已耗尽。")
        for result in results.values()
        if result.get("error_code") == "BUDGET_EXCEEDED"
    }
    if getattr(usage, "exceeded_reason", None):
        reasons.add(f"运行预算达到 {usage.exceeded_reason}。")
    return sorted(reasons)


def _append_budget_scope_limit(
    report: str,
    tasks: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    usage: Any,
) -> str:
    """Keep budget-induced incompleteness explicit in the final Markdown artifact."""
    reasons = _budget_scope_reasons(results, usage)
    if not reasons:
        return report
    limited_tasks = [
        str(task.get("title") or task.get("task_id") or "未命名任务")
        for task in tasks
        if (result := results.get(str(task.get("task_id") or "")))
        and result.get("error_code") == "BUDGET_EXCEEDED"
    ]
    lines = [report.rstrip(), "", "## 执行范围限制"]
    lines.extend(f"- {reason}" for reason in reasons)
    if limited_tasks:
        lines.append(f"- 未执行或提前停止的任务：{'、'.join(limited_tasks)}。")
    lines.append("- 并行任务已启动后无法强制中断，Token、成本和时长预算对同批任务按协作式限制执行。")
    return "\n".join(lines) + "\n"


def _budget_failed_result(task: dict[str, Any], reason: str, attempts: int = 0) -> TaskResult:
    """Make skipped work explicit so Reporter cannot turn a budget stop into success."""
    return failed_task_result(
        task,
        max(attempts, 1),
        "BUDGET_EXCEEDED",
        f"运行预算已耗尽：{reason}。",
        list(task.get("query_history") or [str(task.get("query") or "")]),
    ).model_copy(update={"budget_status": "exceeded"})


def _task_limit_failures(tasks: list[dict[str, Any]], budget: RunBudget) -> dict[str, dict[str, Any]]:
    """Return deterministic failures for plan tasks beyond the strict dispatch limit."""
    return {
        str(task.get("task_id") or ""): _budget_failed_result(task, "MAX_TASKS").model_dump(mode="json")
        for task in tasks[budget.max_tasks :]
        if str(task.get("task_id") or "")
    }


def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    """Generate and validate one task plan before dispatching any search work."""
    topic = str(state.get("topic") or "").strip() or "未命名研究主题"
    session_id = state.get("session_id")
    llm = None
    output = ""
    emit_event(state, EventType.PLANNING, payload={"status": "started"})

    # P1.1 execution starts only after the API has persisted and confirmed a plan version.
    if state.get("confirmed_plan"):
        try:
            plan = TaskPlan.model_validate(state.get("plan") or {})
            existing_run = ResearchRun.model_validate(state.get("run") or {})
            if existing_run.status not in {RunStatus.CONFIRMED, RunStatus.RUNNING}:
                raise ValueError("confirmed plan execution requires a confirmed or running run")
            run = existing_run.model_copy(
                update={
                    "status": RunStatus.RUNNING,
                    "updated_at": utc_now(),
                    "model_versions": model_versions(),
                }
            )
            plan_payload = plan.model_dump(mode="json")
            limit_failures = _task_limit_failures(plan_payload["tasks"], run.budget)
            emit_event(state, EventType.PLANNING, payload={"status": "succeeded", "task_count": len(plan.tasks)})
            return {
                "run": run.model_dump(mode="json"),
                "plan": plan_payload,
                "tasks": plan_payload["tasks"],
                "task_results": limit_failures,
                "loop_count": int(state.get("loop_count", 0)) + 1,
            }
        except Exception as error:
            plan = _build_rejected_plan(topic, StructuredOutputError("CONFIRMED_PLAN_INVALID", _safe_error_message(error)))
            run = _new_run(topic, session_id, plan, RunStatus.FAILED)
            plan_payload = plan.model_dump(mode="json")
            emit_event(
                state,
                EventType.FAILED,
                payload={"stage": "planning", "error_code": "CONFIRMED_PLAN_INVALID"},
            )
            return {
                "run": run.model_dump(mode="json"),
                "plan": plan_payload,
                "tasks": [],
                "output_diagnostics": run.output_diagnostics,
                "loop_count": int(state.get("loop_count", 0)) + 1,
            }

    try:
        llm = _create_llm("planner")
        agent = create_planner_agent(llm)

        try:
            from src.session import get_session_memory

            long_mem = get_long_term_memory()
            short_mem = get_session_memory(session_id) if session_id else get_short_term_memory(llm)
            context = get_memory_context(short_mem) if short_mem else ""
            long_context = "\n".join(search_long_term_memory(topic, long_mem) if long_mem else [])
        except Exception:
            context = ""
            long_context = ""

        prompt = f"""当前研究主题：{topic}

历史上下文：
{context}

长期记忆参考：
{long_context}

请为此主题规划研究任务，并严格遵循系统中的 JSON 输出契约。"""
        output = _strip_reasoning(_message_content(agent.invoke({"messages": [("user", prompt)]})))
        from src.config import get_config

        budget = budget_from_config(get_config())
        repairer = _plan_repairer(_create_llm("repair")) if budget.max_format_repairs else None
        plan = parse_task_plan_with_repair(
            output,
            topic,
            repairer,
            max_repairs=budget.max_format_repairs,
        )
        run = _new_run(
            topic,
            session_id,
            plan,
            RunStatus.RUNNING,
            llm,
            {"planner": _output_diagnostic(output, plan.parse_status)},
        )
    except StructuredOutputError as error:
        plan = _build_rejected_plan(topic, error)
        run = _new_run(
            topic,
            session_id,
            plan,
            RunStatus.FAILED,
            llm,
            {"planner": _output_diagnostic(output, ParseStatus.REJECTED, error.code)},
        )
    except Exception as error:
        structured_error = StructuredOutputError("PLANNER_EXECUTION_FAILED", _safe_error_message(error))
        plan = _build_rejected_plan(topic, structured_error)
        run = _new_run(
            topic,
            session_id,
            plan,
            RunStatus.FAILED,
            llm,
            {"planner": _output_diagnostic(output, ParseStatus.REJECTED, structured_error.code)},
        )

    plan_payload = plan.model_dump(mode="json")
    if plan.parse_status == ParseStatus.REJECTED:
        emit_event(
            state,
            EventType.FAILED,
            payload={"stage": "planning", "error_code": plan.error_code or "PLANNER_REJECTED"},
        )
    else:
        emit_event(state, EventType.PLANNING, payload={"status": "succeeded", "task_count": len(plan.tasks)})
    return {
        "run": run.model_dump(mode="json"),
        "plan": plan_payload,
        "output_diagnostics": run.output_diagnostics,
        "task_results": _task_limit_failures(plan_payload["tasks"], run.budget),
        # Kept while the REST and frontend APIs still consume the legacy list.
        "tasks": plan_payload["tasks"],
        "loop_count": int(state.get("loop_count", 0)) + 1,
    }


def _generate_alternative_queries(query: str, attempt: int) -> list[str]:
    """Generate bounded alternate searches without changing the task identity."""
    english_words = re.findall(r"[a-zA-Z]{3,}", query)
    base_query = " ".join(english_words[:3]) if english_words else query
    alternatives = [base_query]
    if attempt == 2:
        alternatives.extend([f"{query} 技术原理", f"{query} 最新进展"])
    elif attempt >= 3:
        alternatives.extend([f"{base_query} tutorial", f"{base_query} overview"])
    return alternatives


def _result_update(
    result: TaskResult,
    sources: list[Any],
    task: dict[str, Any],
    query: str,
    attempt: int,
    output_diagnostics: dict[str, dict[str, Any]] | None = None,
    task_source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    task_id = result.task_id
    source_payload = {source.source_id: source.model_dump(mode="json") for source in sources}
    refs = task_source_refs or [
        TaskSourceRef(task_id=task_id, source_id=source.source_id, query=query, attempt=attempt).model_dump(mode="json")
        for source in sources
    ]
    return {
        "task_results": {task_id: result.model_dump(mode="json")},
        "sources": source_payload,
        "task_source_refs": {task_id: refs},
        "output_diagnostics": output_diagnostics or {},
    }


def search_summarize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Execute one task and return a keyed result, never an invented fallback."""
    task = dict(state.get("task") or {})
    original_query = str(task.get("query") or "").strip()
    topic = str(state.get("topic") or "")
    previous_result = dict((state.get("task_results") or {}).get(str(task.get("task_id") or "")) or {})
    previous_attempts = int(previous_result.get("attempts") or 0)
    previous_queries = previous_result.get("query_history") or []
    query_history: list[str] = [str(query) for query in previous_queries if str(query).strip()]
    budget = _run_budget(state)
    last_error = ""
    diagnostics: dict[str, dict[str, Any]] = {}
    collected_sources: dict[str, SourceItem] = {}
    source_refs: list[dict[str, Any]] = []
    task_id = str(task.get("task_id") or "unknown_task")
    task_started_at = time.perf_counter()
    cumulative_token_usage: dict[str, int] = {}
    estimated_cost_total = 0.0
    cost_observed = False
    cost_unavailable = False
    cache_hit = False

    def cost_snapshot() -> tuple[float | None, str]:
        if not cost_observed or cost_unavailable:
            return None, "unavailable"
        return round(estimated_cost_total, 12), "estimated"

    def record_response_usage(response: Any, model: str) -> None:
        """Accumulate every task-scoped model response, including format repairs."""
        nonlocal cumulative_token_usage, estimated_cost_total, cost_observed, cost_unavailable
        response_usage = extract_token_usage(response)
        cumulative_token_usage = _merge_token_usage(cumulative_token_usage, response_usage)
        response_cost, response_cost_status = estimate_cost(response_usage, model, pricing)
        if response_cost_status == "estimated" and response_cost is not None:
            estimated_cost_total += response_cost
            cost_observed = True
        else:
            cost_unavailable = True

    def usage_snapshot(attempts: int):
        estimated_cost, cost_status = cost_snapshot()
        return _budget_usage_with_current_task(
            state,
            task_id,
            attempts,
            cumulative_token_usage,
            estimated_cost,
            cost_status,
        )

    def decorate_result(result: TaskResult, attempts: int, budget_status: str) -> TaskResult:
        estimated_cost, cost_status = cost_snapshot()
        return result.model_copy(
            update={
                "attempts": max(result.attempts, attempts),
                "latency_ms": int((time.perf_counter() - task_started_at) * 1000),
                "token_usage": cumulative_token_usage,
                "estimated_cost": estimated_cost,
                "cost_status": cost_status,
                "cache_hit": cache_hit,
                "budget_status": budget_status,
            }
        )

    def event_payload(result: TaskResult) -> dict[str, Any]:
        return {
            "status": result.status.value,
            "attempt": result.attempts,
            "latency_ms": result.latency_ms,
            "token_usage": result.token_usage,
            "estimated_cost": result.estimated_cost,
            "cost_status": result.cost_status,
            "cache_hit": result.cache_hit,
            "budget_status": result.budget_status,
        }

    def budget_failure(reason: str, attempts: int) -> TaskResult:
        return decorate_result(
            _budget_failed_result(task, reason, attempts),
            attempts,
            "exceeded",
        )

    initial_usage = _budget_usage(state)
    emit_event(
        state,
        EventType.TASK_STARTED,
        task_id=task_id,
        payload={
            "status": "started",
            "budget_status": "exceeded" if initial_usage.exhausted else "within_budget",
            "cache_hit": False,
            "token_usage": {},
            "estimated_cost": None,
            "cost_status": "unavailable",
        },
    )
    if initial_usage.exhausted:
        failed = budget_failure(initial_usage.exceeded_reason or "BUDGET_EXCEEDED", previous_attempts + 1)
        payload = event_payload(failed)
        payload["error_code"] = failed.error_code
        emit_event(state, EventType.TASK_FAILED, task_id=task_id, payload=payload)
        return _result_update(failed, [], task, original_query, failed.attempts, diagnostics, source_refs)

    try:
        llm = _create_llm("summarizer")
        agent = create_summarizer_agent(llm)
    except Exception as error:
        failed = decorate_result(
            failed_task_result(
                task,
                previous_attempts + 1,
                "SUMMARIZER_INITIALIZATION_FAILED",
                _safe_error_message(error),
                query_history or [original_query],
            ),
            previous_attempts + 1,
            "within_budget",
        )
        diagnostics[f"{failed.task_id}:init"] = _output_diagnostic(
            "", ParseStatus.REJECTED, "SUMMARIZER_INITIALIZATION_FAILED"
        )
        payload = event_payload(failed)
        payload["error_code"] = "SUMMARIZER_INITIALIZATION_FAILED"
        emit_event(state, EventType.TASK_FAILED, task_id=task_id, payload=payload)
        return _result_update(failed, [], task, original_query, failed.attempts, diagnostics, source_refs)

    from src.config import get_config

    summarizer_model = model_versions().get("summarizer", get_config().llm.model)
    pricing = get_config().routing.pricing
    for attempt in range(1, budget.max_search_attempts + 1):
        cumulative_attempt = previous_attempts + attempt
        before_attempt_usage = usage_snapshot(cumulative_attempt)
        if before_attempt_usage.exhausted:
            failed = budget_failure(before_attempt_usage.exceeded_reason or "BUDGET_EXCEEDED", cumulative_attempt)
            payload = event_payload(failed)
            payload["error_code"] = failed.error_code
            emit_event(state, EventType.TASK_FAILED, task_id=task_id, payload=payload)
            return _result_update(
                failed,
                list(collected_sources.values()),
                task,
                original_query,
                failed.attempts,
                diagnostics,
                source_refs,
            )
        alternatives = _generate_alternative_queries(original_query, attempt)
        query = alternatives[(attempt - 1) % len(alternatives)]
        query_history.append(query)
        task["query_history"] = list(query_history)
        emit_event(
            state,
            EventType.SEARCHING,
            task_id=task_id,
            payload={
                "attempt": cumulative_attempt,
                "cache_hit": cache_hit,
                "token_usage": cumulative_token_usage,
                "estimated_cost": cost_snapshot()[0],
                "cost_status": cost_snapshot()[1],
                "budget_status": "within_budget",
            },
        )
        prompt = f"""任务主题：{topic}
任务名称：{task.get('title', '')}
任务目标：{task.get('intent', '')}
检索查询：{query}

请执行必要的检索，并严格遵循系统中的 JSON 输出契约。"""

        output = ""
        diagnostic_key = f"{str(task.get('task_id') or 'unknown_task')}:{cumulative_attempt}"
        try:
            response = agent.invoke({"messages": [("user", prompt)]})
            current_sources = _collect_tool_sources(response)
            tool_metadata = _collect_tool_metadata(response)
            cache_hit = cache_hit or bool(tool_metadata["cache_hit"])
            record_response_usage(response, summarizer_model)
            for source_id, source in current_sources.items():
                if source_id not in collected_sources:
                    collected_sources[source_id] = source
                    source_refs.append(
                        TaskSourceRef(
                            task_id=str(task.get("task_id") or "unknown_task"),
                            source_id=source_id,
                            query=query,
                            attempt=cumulative_attempt,
                        ).model_dump(mode="json")
                    )
            output = _strip_reasoning(_message_content(response))
            try:
                result, _ = parse_task_result(
                    output,
                    task,
                    cumulative_attempt,
                    query,
                    available_sources=current_sources,
                )
            except StructuredOutputError as parse_error:
                after_response_usage = usage_snapshot(cumulative_attempt)
                if after_response_usage.exhausted:
                    failed = budget_failure(
                        after_response_usage.exceeded_reason or "BUDGET_EXCEEDED",
                        cumulative_attempt,
                    )
                    diagnostics[diagnostic_key] = _output_diagnostic(
                        output, ParseStatus.REJECTED, failed.error_code
                    )
                    payload = event_payload(failed)
                    payload["error_code"] = failed.error_code
                    emit_event(state, EventType.TASK_FAILED, task_id=task_id, payload=payload)
                    return _result_update(
                        failed,
                        list(collected_sources.values()),
                        task,
                        query,
                        failed.attempts,
                        diagnostics,
                        source_refs,
                    )
                repair_error = parse_error
                repair_llm = _create_llm("repair") if budget.max_format_repairs else None
                repair_model = model_versions().get("repair", get_config().llm.model)
                repaired_result: TaskResult | None = None
                for _ in range(budget.max_format_repairs):
                    repair_usage = usage_snapshot(cumulative_attempt)
                    if repair_usage.exhausted:
                        break
                    try:
                        repaired, repair_response = _result_repair(repair_llm, output, set(current_sources))
                        record_response_usage(repair_response, repair_model)
                        repaired_result, _ = parse_task_result(
                            repaired,
                            task,
                            cumulative_attempt,
                            query,
                            parse_status=ParseStatus.REPAIRED,
                            available_sources=current_sources,
                        )
                        break
                    except StructuredOutputError as error:
                        repair_error = error
                else:
                    raise repair_error
                if repaired_result is None:
                    exhausted = usage_snapshot(cumulative_attempt)
                    if exhausted.exhausted:
                        failed = budget_failure(exhausted.exceeded_reason or "BUDGET_EXCEEDED", cumulative_attempt)
                        diagnostics[diagnostic_key] = _output_diagnostic(
                            output, ParseStatus.REJECTED, failed.error_code
                        )
                        payload = event_payload(failed)
                        payload["error_code"] = failed.error_code
                        emit_event(state, EventType.TASK_FAILED, task_id=task_id, payload=payload)
                        return _result_update(
                            failed,
                            list(collected_sources.values()),
                            task,
                            query,
                            failed.attempts,
                            diagnostics,
                            source_refs,
                        )
                    raise repair_error
                result = repaired_result
            current_usage = usage_snapshot(cumulative_attempt)
            result = decorate_result(
                result,
                cumulative_attempt,
                "exhausted" if current_usage.exhausted else "within_budget",
            )
            diagnostics[diagnostic_key] = _output_diagnostic(output, result.parse_status)
            emit_event(state, EventType.TASK_COMPLETED, task_id=task_id, payload=event_payload(result))
            return _result_update(
                result,
                list(collected_sources.values()),
                task,
                query,
                cumulative_attempt,
                diagnostics,
                source_refs,
            )
        except StructuredOutputError as error:
            last_error = f"{error.code}: {_safe_error_message(error)}"
            diagnostics[diagnostic_key] = _output_diagnostic(output, ParseStatus.REJECTED, error.code)
            after_error_usage = usage_snapshot(cumulative_attempt)
            if after_error_usage.exhausted:
                failed = budget_failure(after_error_usage.exceeded_reason or "BUDGET_EXCEEDED", cumulative_attempt)
                payload = event_payload(failed)
                payload["error_code"] = failed.error_code
                emit_event(state, EventType.TASK_FAILED, task_id=task_id, payload=payload)
                return _result_update(
                    failed,
                    list(collected_sources.values()),
                    task,
                    query,
                    failed.attempts,
                    diagnostics,
                    source_refs,
                )
            if attempt < budget.max_search_attempts:
                emit_event(
                    state,
                    EventType.RETRYING,
                    task_id=task_id,
                    payload={
                        "attempt": cumulative_attempt + 1,
                        "error_code": error.code,
                        "cache_hit": cache_hit,
                        "token_usage": cumulative_token_usage,
                        "estimated_cost": cost_snapshot()[0],
                        "cost_status": cost_snapshot()[1],
                        "budget_status": "within_budget",
                    },
                )
        except Exception as error:
            last_error = _safe_error_message(error)
            diagnostics[diagnostic_key] = _output_diagnostic(
                output, ParseStatus.REJECTED, "SUMMARIZER_EXECUTION_FAILED"
            )
            after_error_usage = usage_snapshot(cumulative_attempt)
            if after_error_usage.exhausted:
                failed = budget_failure(after_error_usage.exceeded_reason or "BUDGET_EXCEEDED", cumulative_attempt)
                payload = event_payload(failed)
                payload["error_code"] = failed.error_code
                emit_event(state, EventType.TASK_FAILED, task_id=task_id, payload=payload)
                return _result_update(
                    failed,
                    list(collected_sources.values()),
                    task,
                    query,
                    failed.attempts,
                    diagnostics,
                    source_refs,
                )
            if attempt < budget.max_search_attempts:
                emit_event(
                    state,
                    EventType.RETRYING,
                    task_id=task_id,
                    payload={
                        "attempt": cumulative_attempt + 1,
                        "error_code": "SUMMARIZER_EXECUTION_FAILED",
                        "cache_hit": cache_hit,
                        "token_usage": cumulative_token_usage,
                        "estimated_cost": cost_snapshot()[0],
                        "cost_status": cost_snapshot()[1],
                        "budget_status": "within_budget",
                    },
                )

    attempts = previous_attempts + budget.max_search_attempts
    failed = decorate_result(
        failed_task_result(
            task,
            attempts,
            "SUMMARIZER_OUTPUT_REJECTED",
            last_error or "未获得符合结构化契约的任务结果。",
            query_history or [original_query],
        ),
        attempts,
        "within_budget",
    )
    payload = event_payload(failed)
    payload["error_code"] = failed.error_code or "SUMMARIZER_OUTPUT_REJECTED"
    emit_event(state, EventType.TASK_FAILED, task_id=task_id, payload=payload)
    return _result_update(
        failed,
        list(collected_sources.values()),
        task,
        original_query,
        failed.attempts,
        diagnostics,
        source_refs,
    )


def _ordered_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    plan = state.get("plan") or {}
    return list(plan.get("tasks") or state.get("tasks") or [])


def _build_report_prompt(
    topic: str,
    tasks: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> str:
    task_blocks = []
    referenced_source_ids: set[str] = set()
    for index, task in enumerate(tasks, start=1):
        task_id = str(task.get("task_id") or "")
        result = results.get(task_id)
        if not result:
            task_summary = "证据不足或任务失败：未收到该任务的执行结果。"
        elif result.get("status") != TaskStatus.SUCCEEDED.value:
            task_summary = (
                "证据不足或任务失败："
                f"{result.get('error_message') or result.get('error_code') or '任务未成功完成。'}"
            )
        else:
            task_summary = str(result.get("summary") or "证据不足或任务失败：任务摘要为空。")
            referenced_source_ids.update(result.get("source_ids") or [])

        claim_lines = []
        for claim in (result or {}).get("claims", []):
            claim_source_ids = claim.get("source_ids", [])
            referenced_source_ids.update(claim_source_ids)
            links = []
            for source_id in claim_source_ids:
                source = sources.get(source_id)
                if not source:
                    continue
                url = source.get("canonical_url") or source.get("url")
                title = source.get("title") or source_id
                links.append(f"[{title}]({url})" if url else f"`{source_id}`")
            evidence_status = claim.get("evidence_status", "unverified")
            claim_lines.append(
                f"  - 结论：{claim.get('text', '')}\n"
                f"    - 证据状态：{evidence_status}\n"
                f"    - 来源：{'、'.join(links) if links else '证据不足'}\n"
            )

        task_blocks.append(
            f"### 任务 {index}: {task.get('title', '')}\n"
            f"- 任务目标：{task.get('intent', '')}\n"
            f"- 检索查询：{task.get('query', '')}\n"
            f"- 任务结果：{task_summary}\n"
            f"- 可追溯结论：\n{''.join(claim_lines) if claim_lines else '  - 无可用结论引用。'}\n"
        )

    source_blocks = []
    for source_id in sorted(referenced_source_ids):
        source = sources.get(source_id)
        if not source:
            continue
        url = source.get("canonical_url") or source.get("url")
        title = source.get("title") or url or source_id
        if url:
            source_blocks.append(f"- [{title}]({url})")

    return f"""研究主题：{topic}

任务概览：
{''.join(task_blocks)}

参考来源：
{chr(10).join(source_blocks) if source_blocks else "无可用来源"}

请根据上述任务结果生成最终研究报告。只能使用给定证据；对失败或证据不足的任务必须明确标注，不能将其补写为确定性结论。"""


def _append_source_index(
    report: str,
    results: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> str:
    """Keep a deterministic source_id-to-URL lookup beside the model-written report."""
    source_ids = {
        source_id
        for result in results.values()
        for source_id in result.get("source_ids", [])
    }
    if not source_ids:
        return report

    source_lines = []
    for source_id in sorted(source_ids):
        source = sources.get(source_id)
        if not source:
            continue
        url = source.get("canonical_url") or source.get("url")
        title = source.get("title") or source_id
        link = f"[{title}]({url})" if url else title
        source_lines.append(f"- `{source_id}`: {link}")
    if not source_lines:
        return report
    return f"{report.rstrip()}\n\n## 来源索引\n{chr(10).join(source_lines)}\n"


def _rejected_plan_report(topic: str, plan: dict[str, Any]) -> str:
    reason = plan.get("error_message") or plan.get("error_code") or "任务规划未通过结构化校验。"
    return f"# {topic}\n\n## 执行状态\n任务规划无效，未执行检索。\n\n原因：{reason}\n"


def _report_failure(topic: str, error: Exception | str) -> str:
    return f"# {topic}\n\n## 执行状态\n报告生成失败，保留已完成任务的结构化结果供后续重试。\n\n原因：{_safe_error_message(error)}\n"


def _updated_run(
    state: dict[str, Any],
    status: RunStatus,
    output_diagnostics: dict[str, dict[str, Any]],
    *,
    budget_usage: Any = None,
) -> ResearchRun:
    existing = state.get("run") or {}
    try:
        return ResearchRun.model_validate(existing).model_copy(
            update={
                "status": status,
                "updated_at": utc_now(),
                "output_diagnostics": output_diagnostics,
                "budget_usage": budget_usage or _budget_usage(state),
            }
        )
    except Exception:
        plan = state.get("plan") or {}
        return ResearchRun(
            thread_id=str(state.get("session_id") or ""),
            plan_id=plan.get("plan_id"),
            plan_version=plan.get("plan_version"),
            topic=str(state.get("topic") or "未命名研究主题"),
            status=status,
            budget_usage=budget_usage or _budget_usage(state),
            output_diagnostics=output_diagnostics,
        )


def reporter_node(state: dict[str, Any]) -> dict[str, Any]:
    """Render ordered task results and preserve explicit failure evidence."""
    topic = str(state.get("topic") or "未命名研究主题")
    plan = state.get("plan") or {}
    tasks = _ordered_tasks(state)
    results = dict(state.get("task_results") or {})
    sources = dict(state.get("sources") or {})
    output_diagnostics = dict(state.get("output_diagnostics") or {})
    reporter_diagnostics: dict[str, dict[str, Any]] = {}
    budget_usage = _budget_usage(state)
    budget_limited = bool(_budget_scope_reasons(results, budget_usage))
    emit_event(
        state,
        EventType.REPORTING,
        payload={
            "status": "started",
            "budget_status": "exhausted" if budget_usage.exhausted else "within_budget",
            "token_usage": {"total_tokens": budget_usage.total_tokens},
            "estimated_cost": budget_usage.estimated_cost,
            "cost_status": budget_usage.cost_status,
        },
    )

    if plan.get("parse_status") == ParseStatus.REJECTED.value:
        report = _rejected_plan_report(topic, plan)
        status = RunStatus.FAILED
    else:
        try:
            llm = _create_llm("reporter")
            agent = create_reporter_agent(llm)
            raw_report = _strip_reasoning(
                _message_content(agent.invoke({"messages": [("user", _build_report_prompt(topic, tasks, results, sources))]}))
            )
            if not raw_report:
                raise RuntimeError("Reporter returned an empty response")
            report = _append_source_index(raw_report, results, sources)
            reporter_diagnostics["reporter"] = _output_diagnostic(raw_report, ParseStatus.VALID)
            status = (
                RunStatus.SUCCEEDED
                if all(result.get("status") == TaskStatus.SUCCEEDED.value for result in results.values())
                and len(results) == len(tasks)
                and not budget_limited
                else RunStatus.FAILED
            )
        except Exception as error:
            report = _report_failure(topic, error)
            status = RunStatus.FAILED
            reporter_diagnostics["reporter"] = _output_diagnostic(
                "", ParseStatus.REJECTED, "REPORTER_EXECUTION_FAILED"
            )

    report = _append_budget_scope_limit(report, tasks, results, budget_usage)
    combined_diagnostics = {**output_diagnostics, **reporter_diagnostics}
    run = _updated_run(state, status, combined_diagnostics, budget_usage=budget_usage)
    report_artifact = ReportArtifact(
        run_id=run.run_id,
        markdown=report,
        status=status,
    )
    emit_event(
        state,
        EventType.COMPLETED if status == RunStatus.SUCCEEDED else EventType.FAILED,
        payload={
            "status": status.value,
            "stage": "reporting",
            "budget_status": "exhausted" if budget_limited else "within_budget",
            "token_usage": {"total_tokens": budget_usage.total_tokens},
            "estimated_cost": budget_usage.estimated_cost,
            "cost_status": budget_usage.cost_status,
        },
    )

    summaries = [
        str(results.get(str(task.get("task_id") or ""), {}).get("summary") or "")
        for task in tasks
        if results.get(str(task.get("task_id") or ""), {}).get("summary")
    ]
    try:
        long_mem = get_long_term_memory()
        if long_mem:
            save_research_memory(topic, summaries, report, long_mem)
    except Exception:
        pass

    session_id = state.get("session_id")
    if session_id:
        try:
            from src.memory.short_term import add_to_short_term_memory
            from src.session import get_session_memory

            session_mem = get_session_memory(session_id)
            if session_mem:
                add_to_short_term_memory(
                    session_mem,
                    f"研究主题: {topic}",
                    f"生成了研究报告，包含 {len(results)} 个任务结果。",
                )
        except Exception:
            pass

    return {
        "run": run.model_dump(mode="json"),
        "report": report,
        "report_artifact": report_artifact.model_dump(mode="json"),
        "output_diagnostics": reporter_diagnostics,
    }


def _split_tasks(state: dict[str, Any]) -> list[Send] | str:
    """Dispatch only validated tasks; rejected plans go straight to a failure report."""
    plan = state.get("plan") or {}
    if plan.get("parse_status") == ParseStatus.REJECTED.value:
        return "reporter"
    budget = _run_budget(state)
    tasks = _ordered_tasks(state)[: budget.max_tasks]
    retry_task_id = str(state.get("retry_task_id") or "")
    if retry_task_id:
        tasks = [task for task in tasks if str(task.get("task_id") or "") == retry_task_id]
    results = dict(state.get("task_results") or {})
    tasks = [
        task
        for task in tasks
        if (result := results.get(str(task.get("task_id") or ""))) is None
        or result.get("error_code") != "BUDGET_EXCEEDED"
    ]
    if not tasks:
        return "reporter"
    return [Send("search_summarize", {**state, "task": task}) for task in tasks]


def create_research_graph(checkpointer: Any = None):
    """Create the research workflow, optionally backed by a durable checkpointer."""
    from src.state import ResearchState

    workflow = StateGraph(ResearchState)
    workflow.add_node("planner", planner_node)
    workflow.add_node("search_summarize", search_summarize_node)
    workflow.add_node("reporter", reporter_node)
    workflow.add_edge(START, "planner")
    workflow.add_conditional_edges("planner", _split_tasks, ["search_summarize", "reporter"])
    workflow.add_edge("search_summarize", "reporter")
    workflow.add_edge("reporter", END)
    return workflow.compile(checkpointer=checkpointer)


_research_graph = None


def get_research_graph(checkpointer: Any = None):
    """Get the cached default graph or an isolated graph bound to a supplied checkpointer."""
    if checkpointer is not None:
        return create_research_graph(checkpointer=checkpointer)
    global _research_graph
    if _research_graph is None:
        _research_graph = create_research_graph()
    return _research_graph
