"""Research workflow with versioned artifacts and deterministic aggregation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.agents import create_planner_agent, create_reporter_agent, create_summarizer_agent
from src.memory.long_term import get_long_term_memory, save_research_memory, search_long_term_memory
from src.memory.short_term import get_memory_context, get_short_term_memory
from src.state import (
    ParseStatus,
    ReportArtifact,
    ResearchRun,
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


MAX_SEARCH_ATTEMPTS = 3


def _create_llm():
    """Create an LLM instance from the current configuration."""
    from src.llm import create_llm

    return create_llm()


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


def _format_repairer(llm: Any, schema: str) -> Callable[[str], str]:
    """Return a one-shot, format-only repair callback for validation helpers."""

    def repair(raw_output: str) -> str:
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
        return _strip_reasoning(_message_content(response))

    return repair


def _plan_repairer(llm: Any) -> Callable[[str], str]:
    return _format_repairer(
        llm,
        '{"tasks":[{"title":"任务名称","intent":"任务目标","query":"检索查询"}]}',
    )


def _result_repair(llm: Any, raw_output: str, available_source_ids: set[str]) -> str:
    source_ids = ", ".join(sorted(available_source_ids)) or "(无)"
    repairer = _format_repairer(
        llm,
        '{"summary":"至少 200 字的事实性总结","claims":[{"text":"可验证结论",'
        '"source_ids":["src_xxx"],"evidence_status":"supported"}]}。'
        f"允许的 source_id：{source_ids}",
    )
    return repairer(raw_output)


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
    model_name = str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or "")
    return ResearchRun(
        thread_id=session_id or "",
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        topic=topic,
        status=status,
        model_versions={"default": model_name} if model_name else {},
        prompt_versions={"planner": "p0.1", "summarizer": "p0.2", "reporter": "p0.2"},
        output_diagnostics=output_diagnostics or {},
    )


def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    """Generate and validate one task plan before dispatching any search work."""
    topic = str(state.get("topic") or "").strip() or "未命名研究主题"
    session_id = state.get("session_id")
    llm = None
    output = ""

    try:
        llm = _create_llm()
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
        plan = parse_task_plan_with_repair(output, topic, _plan_repairer(llm))
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
    return {
        "run": run.model_dump(mode="json"),
        "plan": plan_payload,
        "output_diagnostics": run.output_diagnostics,
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
    query_history: list[str] = []
    last_error = ""
    repair_attempted = False
    diagnostics: dict[str, dict[str, Any]] = {}
    collected_sources: dict[str, SourceItem] = {}
    source_refs: list[dict[str, Any]] = []

    try:
        llm = _create_llm()
        agent = create_summarizer_agent(llm)
    except Exception as error:
        failed = failed_task_result(
            task,
            1,
            "SUMMARIZER_INITIALIZATION_FAILED",
            _safe_error_message(error),
            query_history or [original_query],
        )
        diagnostics[f"{failed.task_id}:init"] = _output_diagnostic(
            "", ParseStatus.REJECTED, "SUMMARIZER_INITIALIZATION_FAILED"
        )
        return _result_update(failed, [], task, original_query, 1, diagnostics, source_refs)

    for attempt in range(1, MAX_SEARCH_ATTEMPTS + 1):
        alternatives = _generate_alternative_queries(original_query, attempt)
        query = alternatives[(attempt - 1) % len(alternatives)]
        query_history.append(query)
        task["query_history"] = list(query_history)
        prompt = f"""任务主题：{topic}
任务名称：{task.get('title', '')}
任务目标：{task.get('intent', '')}
检索查询：{query}

请执行必要的检索，并严格遵循系统中的 JSON 输出契约。"""

        output = ""
        diagnostic_key = f"{str(task.get('task_id') or 'unknown_task')}:{attempt}"
        try:
            response = agent.invoke({"messages": [("user", prompt)]})
            current_sources = _collect_tool_sources(response)
            for source_id, source in current_sources.items():
                if source_id not in collected_sources:
                    collected_sources[source_id] = source
                    source_refs.append(
                        TaskSourceRef(
                            task_id=str(task.get("task_id") or "unknown_task"),
                            source_id=source_id,
                            query=query,
                            attempt=attempt,
                        ).model_dump(mode="json")
                    )
            output = _strip_reasoning(_message_content(response))
            try:
                result, _ = parse_task_result(
                    output,
                    task,
                    attempt,
                    query,
                    available_sources=current_sources,
                )
            except StructuredOutputError as parse_error:
                if repair_attempted:
                    raise parse_error
                repair_attempted = True
                repaired = _result_repair(llm, output, set(current_sources))
                result, _ = parse_task_result(
                    repaired,
                    task,
                    attempt,
                    query,
                    parse_status=ParseStatus.REPAIRED,
                    available_sources=current_sources,
                )
            diagnostics[diagnostic_key] = _output_diagnostic(output, result.parse_status)
            return _result_update(
                result,
                list(collected_sources.values()),
                task,
                query,
                attempt,
                diagnostics,
                source_refs,
            )
        except StructuredOutputError as error:
            last_error = f"{error.code}: {_safe_error_message(error)}"
            diagnostics[diagnostic_key] = _output_diagnostic(output, ParseStatus.REJECTED, error.code)
        except Exception as error:
            last_error = _safe_error_message(error)
            diagnostics[diagnostic_key] = _output_diagnostic(
                output, ParseStatus.REJECTED, "SUMMARIZER_EXECUTION_FAILED"
            )

    failed = failed_task_result(
        task,
        MAX_SEARCH_ATTEMPTS,
        "SUMMARIZER_OUTPUT_REJECTED",
        last_error or "未获得符合结构化契约的任务结果。",
        query_history or [original_query],
    )
    return _result_update(
        failed,
        list(collected_sources.values()),
        task,
        original_query,
        MAX_SEARCH_ATTEMPTS,
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
) -> ResearchRun:
    existing = state.get("run") or {}
    try:
        return ResearchRun.model_validate(existing).model_copy(
            update={
                "status": status,
                "updated_at": utc_now(),
                "output_diagnostics": output_diagnostics,
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

    if plan.get("parse_status") == ParseStatus.REJECTED.value:
        report = _rejected_plan_report(topic, plan)
        status = RunStatus.FAILED
    else:
        try:
            llm = _create_llm()
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
                else RunStatus.FAILED
            )
        except Exception as error:
            report = _report_failure(topic, error)
            status = RunStatus.FAILED
            reporter_diagnostics["reporter"] = _output_diagnostic(
                "", ParseStatus.REJECTED, "REPORTER_EXECUTION_FAILED"
            )

    combined_diagnostics = {**output_diagnostics, **reporter_diagnostics}
    run = _updated_run(state, status, combined_diagnostics)
    report_artifact = ReportArtifact(
        run_id=run.run_id,
        markdown=report,
        status=status,
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
    tasks = _ordered_tasks(state)
    if not tasks:
        return "reporter"
    return [Send("search_summarize", {**state, "task": task}) for task in tasks]


def create_research_graph():
    """Create the P0.2 research workflow graph."""
    from src.state import ResearchState

    workflow = StateGraph(ResearchState)
    workflow.add_node("planner", planner_node)
    workflow.add_node("search_summarize", search_summarize_node)
    workflow.add_node("reporter", reporter_node)
    workflow.add_edge(START, "planner")
    workflow.add_conditional_edges("planner", _split_tasks, ["search_summarize", "reporter"])
    workflow.add_edge("search_summarize", "reporter")
    workflow.add_edge("reporter", END)
    return workflow.compile()


_research_graph = None


def get_research_graph():
    """Get the cached research graph."""
    global _research_graph
    if _research_graph is None:
        _research_graph = create_research_graph()
    return _research_graph
