"""FastAPI entry point for LangGraph Deep Research"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.config import get_config
from src.graph.research import _safe_error_message, create_research_graph, get_research_graph
from src.memory.long_term import create_long_term_memory, search_long_term_memory
from src.memory.short_term import create_short_term_memory, get_short_term_memory
from src.session import (
    create_session, get_session, add_message, delete_session,
    get_session_memory, set_session_memory, ChatMessage, SessionState,
)
from src.agents.router import route_intent
from src.agents.followup import handle_followup, handle_general
from src.memory.short_term import add_to_short_term_memory, create_short_term_memory
from src.events import EventPublisher, EventType, ResearchEvent, encode_sse, register_publisher, unregister_publisher
from src.repository import InvalidStateTransitionError, NotFoundError, RepositoryError, SQLiteRepository
from src.state import ParseStatus, TaskItem, TaskPlan, TaskStatus
from src.tracing import build_trace_callbacks

# 配置日志
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <4}</level> | <level>{message}</level>",
    colorize=True,
)

# 历史记录存储目录（使用绝对路径，基于项目根目录）
HISTORY_DIR = Path(__file__).parent.parent / "research_history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


class ResearchRequest(BaseModel):
    """研究请求"""
    topic: str = Field(..., description="Research topic")
    search_api: Optional[str] = Field(default=None, description="Search API override")
    session_id: Optional[str] = Field(default=None, description="Optional chat session to update")


class ResearchResponse(BaseModel):
    """研究响应"""
    report_markdown: str = Field(..., description="Markdown-formatted research report")
    todo_items: list[dict[str, Any]] = Field(default_factory=list, description="Task items")


class HistoryItem(BaseModel):
    """历史记录"""
    id: str
    topic: str
    report: str
    tasks: List[dict[str, Any]]
    created_at: str


class ChatRequest(BaseModel):
    """Chat message request"""
    message: str = Field(..., description="User message")


class ChatResponse(BaseModel):
    """Chat message response"""
    reply: str = Field(..., description="AI reply content")
    message_type: str = Field(default="text", description="Message type: text, research_report, task_plan")
    tasks: Optional[List[dict]] = Field(default=None, description="Task list if applicable")


class PlanUpdateRequest(BaseModel):
    """A user-edited draft; saving it always creates a new immutable plan version."""

    topic: Optional[str] = Field(default=None, max_length=1_000)
    tasks: list[dict[str, Any]] = Field(..., min_length=1, max_length=7)


class RunCreateRequest(BaseModel):
    """Reference to one confirmed plan version."""

    plan_id: str = Field(..., min_length=1)
    plan_version: int = Field(..., ge=1)


def _save_history(topic: str, report: str, tasks: List[dict]) -> str:
    """保存研究历史到文件"""
    import time
    history_id = f"research_{int(time.time() * 1000)}"
    history_data = {
        "id": history_id,
        "topic": topic,
        "report": report,
        "tasks": tasks,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    file_path = HISTORY_DIR / f"{history_id}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)
    return history_id


def _get_history_list() -> List[dict]:
    """获取历史记录列表"""
    history_list = []
    for file_path in HISTORY_DIR.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                history_list.append({
                    "id": data.get("id"),
                    "topic": data.get("topic"),
                    "created_at": data.get("created_at")
                })
        except Exception:
            continue
    # 按时间倒序排列
    history_list.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return history_list


def _get_history(history_id: str) -> Optional[dict]:
    """获取单条历史记录"""
    file_path = HISTORY_DIR / f"{history_id}.json"
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _create_plan_only(topic: str) -> dict:
    """只生成经过 P0.1 契约校验的任务规划，不执行检索。"""
    from src.graph.research import planner_node

    state = planner_node({"topic": topic})
    plan = state.get("plan", {})
    if plan.get("parse_status") == "rejected":
        reason = plan.get("error_message") or plan.get("error_code") or "任务规划未通过校验。"
        return {
            "report_markdown": f"## 任务规划失败\n\n{reason}",
            "todo_items": [],
        }

    return {
        "report_markdown": "",
        "todo_items": state.get("tasks", []),
    }


def _generate_valid_plan(topic: str) -> TaskPlan:
    """Generate a validated plan without running search tasks."""
    from src.graph.research import planner_node

    state = planner_node({"topic": topic})
    plan = TaskPlan.model_validate(state.get("plan") or {})
    if plan.parse_status == ParseStatus.REJECTED:
        reason = plan.error_message or plan.error_code or "任务规划未通过校验。"
        raise InvalidStateTransitionError(reason)
    return plan


def _edited_plan(current: dict[str, Any], payload: PlanUpdateRequest) -> TaskPlan:
    """Validate an edited draft and reset task execution state for its new plan version."""
    existing = TaskPlan.model_validate(current["plan"])
    tasks = []
    for index, raw_task in enumerate(payload.tasks, start=1):
        task = TaskItem.model_validate(raw_task)
        tasks.append(task.model_copy(update={"id": index, "status": TaskStatus.PLANNED}))
    return TaskPlan(topic=(payload.topic or existing.topic), tasks=tasks)


def _run_config(
    run_id: str,
    *,
    callbacks: list[Any] | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"configurable": {"thread_id": run_id}}
    if callbacks:
        config["callbacks"] = callbacks
    if metadata:
        config["metadata"] = metadata
    return config


def _trace_metadata(run: dict[str, Any], *, session_id: str | None = None) -> dict[str, str]:
    """Build low-cardinality metadata safe to send to an optional tracer."""
    model_versions = run.get("model_versions") or {}
    prompt_versions = run.get("prompt_versions") or {}
    return {
        "run_id": str(run.get("run_id") or ""),
        "session_id": str(session_id or ""),
        "model_version": str(model_versions.get("default") or ""),
        "prompt_version": str(prompt_versions.get("reporter") or ""),
        "redacted": "true",
    }


def _execute_persisted_run(
    repository: SQLiteRepository,
    graph_factory: Callable[..., Any],
    run_id: str,
    *,
    retry_task_id: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Execute or resume a confirmed run, persisting final graph artifacts atomically afterwards."""
    state = repository.prepare_task_retry(run_id, retry_task_id) if retry_task_id else repository.execution_state(run_id)
    repository.mark_run_running(run_id)
    publisher = EventPublisher(repository, run_id)
    register_publisher(publisher)
    try:
        run = state.get("run") or {}
        metadata = _trace_metadata(run, session_id=state.get("session_id"))
        callbacks = build_trace_callbacks(get_config().tracing, metadata)
        graph = graph_factory(checkpointer=repository.checkpointer)
        config = _run_config(run_id, callbacks=callbacks, metadata=metadata)
        graph_input: dict[str, Any] | None = state
        if resume and hasattr(graph, "get_state"):
            snapshot = graph.get_state(config)
            if getattr(snapshot, "values", None):
                graph_input = None
        result = graph.invoke(graph_input, config=config)
        if not isinstance(result, dict):
            raise RepositoryError("research graph must return a dictionary state")
        return repository.persist_graph_result(run_id, result)
    except Exception as error:
        publisher.publish(
            EventType.FAILED,
            payload={"stage": "execution", "error_message": _safe_error_message(error)},
        )
        raise
    finally:
        unregister_publisher(run_id)
        publisher.close()


def _repository_http_error(error: RepositoryError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(error, NotFoundError) else 409, detail=str(error))


def create_app(
    repository: SQLiteRepository | None = None,
    graph_factory: Callable[..., Any] = create_research_graph,
    *,
    initialize_services: bool = True,
) -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(title="LangGraph Deep Researcher")
    app.state.repository = repository
    app.state.owns_repository = repository is None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def init_services():
        """初始化服务"""
        config = get_config()
        if app.state.repository is None:
            app.state.repository = SQLiteRepository(config.storage.sqlite_path)

        if not initialize_services:
            return

        # 初始化长期记忆
        create_long_term_memory(
            persist_directory=config.memory.long_term_persist_dir,
            k=config.memory.long_term_k,
        )

        # 初始化短期记忆
        from src.llm import create_llm
        llm = create_llm()
        create_short_term_memory(llm, config.memory.short_term_max_tokens)

        logger.info(f"LangGraph Deep Researcher initialized")
        logger.info(f"LLM: {config.llm.model} @ {config.llm.base_url}")
        logger.info(
            f"Embeddings: {config.embeddings.provider}/{config.embeddings.model} "
            f"on {config.embeddings.device} "
            f"(batch={config.embeddings.batch_size}, max_length={config.embeddings.max_length})"
        )
        logger.info(f"ChromaDB: {config.memory.long_term_persist_dir}")

    @app.on_event("shutdown")
    def close_repository():
        repository_instance = app.state.repository
        if app.state.owns_repository and repository_instance is not None:
            repository_instance.close()

    def active_repository() -> SQLiteRepository:
        repository_instance = app.state.repository
        if repository_instance is None:
            repository_instance = SQLiteRepository(get_config().storage.sqlite_path)
            app.state.repository = repository_instance
        return repository_instance

    @app.get("/healthz")
    def health_check() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/plans")
    def create_persisted_plan(payload: ResearchRequest) -> dict[str, Any]:
        try:
            return active_repository().create_plan(_generate_valid_plan(payload.topic))
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            logger.exception("Plan generation failed")
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.get("/plans/{plan_id}/versions/{plan_version}")
    def get_persisted_plan(plan_id: str, plan_version: int) -> dict[str, Any]:
        try:
            return active_repository().get_plan(plan_id, plan_version)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.put("/plans/{plan_id}/versions/{plan_version}")
    def update_persisted_plan(plan_id: str, plan_version: int, payload: PlanUpdateRequest) -> dict[str, Any]:
        try:
            repository_instance = active_repository()
            current = repository_instance.get_plan(plan_id, plan_version)
            return repository_instance.update_plan(plan_id, plan_version, _edited_plan(current, payload))
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/plans/{plan_id}/versions/{plan_version}/confirm")
    def confirm_persisted_plan(plan_id: str, plan_version: int) -> dict[str, Any]:
        try:
            return active_repository().confirm_plan(plan_id, plan_version)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.post("/plans/{plan_id}/versions/{plan_version}/cancel")
    def cancel_persisted_plan(plan_id: str, plan_version: int) -> dict[str, Any]:
        try:
            return active_repository().cancel_plan(plan_id, plan_version)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.post("/runs")
    def create_persisted_run(payload: RunCreateRequest) -> dict[str, Any]:
        try:
            repository_instance = active_repository()
            run = repository_instance.create_run(payload.plan_id, payload.plan_version)
            return _execute_persisted_run(repository_instance, graph_factory, run["run"]["run_id"])
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            logger.exception("Run execution failed")
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post("/runs/{run_id}/resume")
    def resume_persisted_run(run_id: str) -> dict[str, Any]:
        try:
            return _execute_persisted_run(active_repository(), graph_factory, run_id, resume=True)
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            logger.exception("Run resume failed")
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post("/runs/{run_id}/tasks/{task_id}/retry")
    def retry_persisted_task(run_id: str, task_id: str) -> dict[str, Any]:
        try:
            return _execute_persisted_run(
                active_repository(), graph_factory, run_id, retry_task_id=task_id
            )
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            logger.exception("Task retry failed")
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post("/runs/{run_id}/cancel")
    def cancel_persisted_run(run_id: str) -> dict[str, Any]:
        try:
            return active_repository().cancel_run(run_id)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.get("/runs/{run_id}")
    def get_persisted_run(run_id: str) -> dict[str, Any]:
        try:
            return active_repository().get_run(run_id)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.post("/research", response_model=ResearchResponse)
    def run_research(payload: ResearchRequest) -> ResearchResponse:
        """同步执行研究"""
        try:
            repository_instance = active_repository()
            plan = repository_instance.create_plan(_generate_valid_plan(payload.topic))
            repository_instance.confirm_plan(plan["plan"]["plan_id"], plan["plan"]["plan_version"])
            created_run = repository_instance.create_run(plan["plan"]["plan_id"], plan["plan"]["plan_version"])
            result = _execute_persisted_run(repository_instance, graph_factory, created_run["run"]["run_id"])
        except Exception as exc:
            logger.exception("Research failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        reports = result.get("report_versions") or []
        report = str(reports[-1].get("markdown") or "") if reports else ""
        tasks = result.get("plan", {}).get("tasks", [])

        # 保存到历史记录
        _save_history(payload.topic, report, tasks)

        return ResearchResponse(
            report_markdown=report,
            todo_items=tasks,
        )

    @app.get("/history", response_model=List[dict])
    def get_history():
        """获取历史研究列表"""
        logger.info(f"HISTORY_DIR: {HISTORY_DIR}, exists: {HISTORY_DIR.exists()}")
        result = _get_history_list()
        logger.info(f"History list: {result}")
        return result

    @app.get("/history/{history_id}")
    def get_history_detail(history_id: str):
        """获取历史研究详情"""
        history = _get_history(history_id)
        if not history:
            raise HTTPException(status_code=404, detail="历史记录不存在")
        return history

    @app.post("/plan", response_model=ResearchResponse)
    def create_plan(payload: ResearchRequest) -> ResearchResponse:
        """只生成任务规划，不保存历史"""
        try:
            result = _create_plan_only(payload.topic)
        except Exception as exc:
            logger.exception("Planning failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return ResearchResponse(
            report_markdown=result["report_markdown"],
            todo_items=result["todo_items"],
        )

    @app.post("/research/stream")
    def stream_research(payload: ResearchRequest) -> StreamingResponse:
        """Execute the compatibility one-click flow and emit standard SSE events."""

        def event_iterator() -> Iterator[str]:
            repository_instance = active_repository()
            publisher: EventPublisher | None = None
            run_id = "unassigned"
            try:
                plan = repository_instance.create_plan(_generate_valid_plan(payload.topic))
                confirmed = repository_instance.confirm_plan(
                    plan["plan"]["plan_id"], plan["plan"]["plan_version"]
                )
                created = repository_instance.create_run(
                    confirmed["plan"]["plan_id"], confirmed["plan"]["plan_version"]
                )
                run_id = created["run"]["run_id"]
                publisher = EventPublisher(repository_instance, run_id)
                register_publisher(publisher)
                publisher.publish(
                    EventType.PLAN_CONFIRMED,
                    payload={"plan_version": confirmed["plan"]["plan_version"]},
                    persist=False,
                )
                repository_instance.mark_run_running(run_id)
                state = repository_instance.execution_state(run_id)
                if payload.session_id:
                    state["session_id"] = payload.session_id
                metadata = _trace_metadata(state["run"], session_id=payload.session_id)
                callbacks = build_trace_callbacks(get_config().tracing, metadata)
                graph = graph_factory(checkpointer=repository_instance.checkpointer)
                config = _run_config(run_id, callbacks=callbacks, metadata=metadata)
                final_state: dict[str, Any] | None = None
                for chunk in graph.stream(state, config=config, stream_mode="values"):
                    if isinstance(chunk, dict):
                        final_state = chunk
                    for event in publisher.drain():
                        yield encode_sse(event)
                if final_state is None:
                    raise RepositoryError("research graph produced no final state")
                result = repository_instance.persist_graph_result(run_id, final_state)
                for event in publisher.drain():
                    yield encode_sse(event)
                reports = result.get("report_versions") or []
                report = str(reports[-1].get("markdown") or "") if reports else ""
                if payload.session_id and get_session(payload.session_id):
                    session = get_session(payload.session_id)
                    session.current_topic = payload.topic
                    session.last_report = report
                    session.last_tasks = result.get("plan", {}).get("tasks", [])
                    add_message(payload.session_id, ChatMessage(role="user", content=payload.topic))
                    add_message(
                        payload.session_id,
                        ChatMessage(
                            role="assistant",
                            content=report,
                            message_type="research_report",
                            tasks=session.last_tasks,
                        ),
                    )
                final_event = publisher.publish(
                    EventType.COMPLETED,
                    payload={
                        "status": result["run"]["status"],
                        "report_markdown": report,
                        "report_version": reports[-1].get("report_version") if reports else None,
                    },
                    persist=False,
                )
                yield encode_sse(final_event)
            except Exception as exc:
                logger.exception("Streaming research failed")
                if publisher is not None:
                    failed_event = publisher.publish(
                        EventType.FAILED,
                        payload={"stage": "stream", "error_message": _safe_error_message(exc)},
                    )
                else:
                    failed_event = ResearchEvent(
                        run_id=run_id,
                        type=EventType.FAILED,
                        payload={"stage": "stream", "error_message": _safe_error_message(exc)},
                    )
                yield encode_sse(failed_event)
            finally:
                if publisher is not None:
                    unregister_publisher(run_id)
                    publisher.close()

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.post("/sessions", response_model=SessionState)
    def api_create_session():
        """Create a new chat session"""
        session = create_session()

        # Create per-session short-term memory
        config = get_config()
        from src.llm import create_llm
        llm = create_llm()
        memory = create_short_term_memory(llm, config.memory.short_term_max_tokens)
        set_session_memory(session.id, memory)

        # Add welcome message
        welcome = ChatMessage(
            role="assistant",
            content="你好！我是 LangGraph 深度研究助手。你可以：\n\n"
                    "- 输入任何主题，我会为你执行深度研究\n"
                    "- 对研究结果追问或要求深入分析\n"
                    "- 要求调整研究任务的方向\n\n"
                    "请问你想研究什么？",
            message_type="text",
        )
        add_message(session.id, welcome)

        return get_session(session.id)

    @app.post("/sessions/{session_id}/chat", response_model=ChatResponse)
    def api_chat(session_id: str, payload: ChatRequest):
        """Send a message in a chat session"""
        session = get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        user_msg = payload.message.strip()
        if not user_msg:
            raise HTTPException(status_code=400, detail="消息不能为空")

        # Add user message to session
        add_message(session_id, ChatMessage(role="user", content=user_msg))

        # Save to session's short-term memory
        session_mem = get_session_memory(session_id)

        # Create LLM
        from src.llm import create_llm
        llm = create_llm()

        # Route intent
        try:
            intent = route_intent(
                message=user_msg,
                has_report=session.last_report is not None,
                has_tasks=session.last_tasks is not None,
                llm=llm,
            )
        except Exception:
            intent = "new_research"

        logger.info(f"Session {session_id}: intent={intent}, message={user_msg[:50]}")

        try:
            if intent == "new_research":
                # Execute full research
                graph = get_research_graph()
                result = graph.invoke({
                    "topic": user_msg,
                    "session_id": session_id,
                })

                report = result.get("report", "研究完成，但未能生成报告。")
                tasks = result.get("tasks", [])

                # Save to short-term memory
                if session_mem:
                    try:
                        add_to_short_term_memory(session_mem, user_msg, report[:500])
                    except Exception:
                        pass

                # Update session state
                session.current_topic = user_msg
                session.last_report = report
                session.last_tasks = tasks

                # Save to history
                _save_history(user_msg, report, tasks)

                ai_msg = ChatMessage(
                    role="assistant",
                    content=report,
                    message_type="research_report",
                    tasks=tasks,
                )
                add_message(session_id, ai_msg)

                return ChatResponse(
                    reply=report,
                    message_type="research_report",
                    tasks=tasks,
                )

            elif intent == "follow_up":
                reply = handle_followup(user_msg, session, session_mem, llm)

                # Save to short-term memory
                if session_mem:
                    try:
                        add_to_short_term_memory(session_mem, user_msg, reply[:500])
                    except Exception:
                        pass

                ai_msg = ChatMessage(role="assistant", content=reply, message_type="text")
                add_message(session_id, ai_msg)

                return ChatResponse(reply=reply, message_type="text")

            elif intent == "refine_tasks":
                # Execute research with context from previous tasks
                graph = get_research_graph()
                combined_topic = f"{session.current_topic}\n\n用户补充要求：{user_msg}\n之前任务列表：{session.last_tasks}"
                result = graph.invoke({
                    "topic": combined_topic,
                    "session_id": session_id,
                })

                report = result.get("report", "研究完成，但未能生成报告。")
                tasks = result.get("tasks", [])

                if session_mem:
                    try:
                        add_to_short_term_memory(session_mem, user_msg, report[:500])
                    except Exception:
                        pass

                session.last_report = report
                session.last_tasks = tasks

                _save_history(f"{session.current_topic} (refined)", report, tasks)

                ai_msg = ChatMessage(
                    role="assistant",
                    content=report,
                    message_type="research_report",
                    tasks=tasks,
                )
                add_message(session_id, ai_msg)

                return ChatResponse(
                    reply=report,
                    message_type="research_report",
                    tasks=tasks,
                )

            else:  # general
                reply = handle_general(user_msg, session_mem, llm)

                if session_mem:
                    try:
                        add_to_short_term_memory(session_mem, user_msg, reply[:500])
                    except Exception:
                        pass

                ai_msg = ChatMessage(role="assistant", content=reply, message_type="text")
                add_message(session_id, ai_msg)

                return ChatResponse(reply=reply, message_type="text")

        except Exception as exc:
            logger.exception(f"Chat processing failed for session {session_id}")
            error_reply = f"处理消息时出错：{str(exc)}"
            ai_msg = ChatMessage(role="assistant", content=error_reply, message_type="text")
            add_message(session_id, ai_msg)
            return ChatResponse(reply=error_reply, message_type="text")

    @app.get("/sessions/{session_id}", response_model=SessionState)
    def api_get_session(session_id: str):
        """Get session history"""
        session = get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        return session

    @app.delete("/sessions/{session_id}")
    def api_delete_session(session_id: str):
        """Delete a session"""
        if not get_session(session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        delete_session(session_id)
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
