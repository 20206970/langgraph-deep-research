"""FastAPI entry point for LangGraph Deep Research"""

import sys
from typing import Any, Callable, Dict, Iterator, List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.config import get_config
from src.auth import (
    CredentialRequest,
    CurrentUser,
    TokenResponse,
    create_access_token,
    hash_password,
    require_current_user,
    verify_password,
)
from src.budget import budget_from_config
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
from src.state import ParseStatus, TaskItem, TaskPlan, TaskStatus, new_id
from src.tracing import build_trace_callbacks
from src.documents.models import DocumentDetailView, DocumentListResponse, DocumentVersionView, IngestionJobView, StorageUsageView
from src.documents.index import DocumentIndexError, DocumentIndexService
from src.documents.repository import DocumentQuotaExceededError, DocumentRepository
from src.documents.storage import DocumentStorage, DocumentStorageError, DocumentTooLargeError, UnsupportedDocumentTypeError

# 配置日志
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <4}</level> | <level>{message}</level>",
    colorize=True,
)

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


def _history_detail(run_record: dict[str, Any]) -> dict[str, Any]:
    """Adapt an owned persisted run to the frontend's existing history contract."""

    reports = run_record.get("report_versions") or []
    latest_report = reports[-1] if reports else {}
    return {
        "id": run_record["run"]["run_id"],
        "topic": run_record["run"]["topic"],
        "report": latest_report.get("markdown", ""),
        "tasks": run_record.get("plan", {}).get("tasks", []),
        "created_at": run_record["run"].get("created_at", ""),
    }


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
    budget = run.get("budget") or {}
    metadata = {
        "run_id": str(run.get("run_id") or ""),
        "session_id": str(session_id or ""),
        "model_version": str(model_versions.get("summarizer") or model_versions.get("default") or ""),
        "prompt_version": str(prompt_versions.get("reporter") or ""),
        "budget_max_tasks": str(budget.get("max_tasks") or ""),
        "budget_max_search_attempts": str(budget.get("max_search_attempts") or ""),
        "budget_token_limit_enabled": str(budget.get("max_total_tokens") is not None).lower(),
        "budget_cost_limit_enabled": str(budget.get("max_estimated_cost") is not None).lower(),
        "redacted": "true",
    }
    for role in ("router", "planner", "summarizer", "reporter", "repair", "judge"):
        if model_versions.get(role):
            metadata[f"model_{role}"] = str(model_versions[role])
    return metadata


def _execute_persisted_run(
    repository: SQLiteRepository,
    graph_factory: Callable[..., Any],
    run_id: str,
    *,
    owner_id: str,
    retry_task_id: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Execute or resume a confirmed run, persisting final graph artifacts atomically afterwards."""
    state = (
        repository.prepare_task_retry(run_id, retry_task_id, owner_id=owner_id)
        if retry_task_id
        else repository.execution_state(run_id, owner_id=owner_id)
    )
    repository.mark_run_running(run_id, owner_id=owner_id)
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
        return repository.persist_graph_result(run_id, result, owner_id=owner_id)
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
    document_repository: DocumentRepository | None = None,
    document_storage: DocumentStorage | None = None,
    document_index: DocumentIndexService | None = None,
) -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(title="LangGraph Deep Researcher")
    app.state.repository = repository
    app.state.owns_repository = repository is None
    app.state.document_repository = document_repository
    app.state.document_storage = document_storage
    app.state.document_index = document_index

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
        if app.state.document_repository is None:
            app.state.document_repository = DocumentRepository(app.state.repository.database_path, config.documents)
        if app.state.document_storage is None:
            app.state.document_storage = DocumentStorage(config.documents)

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

    def active_document_repository() -> DocumentRepository:
        repository_instance = app.state.document_repository
        if repository_instance is None:
            repository_instance = DocumentRepository(active_repository().database_path, get_config().documents)
            app.state.document_repository = repository_instance
        return repository_instance

    def active_document_storage() -> DocumentStorage:
        storage = app.state.document_storage
        if storage is None:
            storage = DocumentStorage(get_config().documents)
            app.state.document_storage = storage
        return storage

    def active_document_index() -> DocumentIndexService:
        index = app.state.document_index
        if index is None:
            config = get_config()
            index = DocumentIndexService(active_document_repository(), config.documents, config.embeddings)
            app.state.document_index = index
        return index

    def document_detail(record: dict[str, Any]) -> DocumentDetailView:
        return DocumentDetailView(
            **record["document"],
            current_version=record.get("current_version"),
            versions=record.get("versions") or [],
            jobs=record.get("jobs") or [],
        )

    def document_http_error(error: Exception) -> HTTPException:
        if isinstance(error, DocumentTooLargeError | DocumentQuotaExceededError):
            return HTTPException(status_code=413, detail=str(error))
        if isinstance(error, UnsupportedDocumentTypeError):
            return HTTPException(status_code=415, detail=str(error))
        if isinstance(error, DocumentStorageError):
            return HTTPException(status_code=400, detail=str(error))
        if isinstance(error, RepositoryError):
            return _repository_http_error(error)
        return HTTPException(status_code=500, detail="document lifecycle operation failed")

    @app.get("/healthz")
    def health_check() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/register", response_model=TokenResponse, status_code=201)
    def register(payload: CredentialRequest) -> TokenResponse:
        repository_instance = active_repository()
        if repository_instance.get_user_by_username(payload.username) is not None:
            raise HTTPException(status_code=409, detail="username already exists")
        try:
            created = repository_instance.create_user(payload.username, hash_password(payload.password))
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        user = CurrentUser(user_id=created["user_id"], username=created["username"])
        return TokenResponse(access_token=create_access_token(user, get_config().auth), user=user)

    @app.post("/auth/login", response_model=TokenResponse)
    def login(payload: CredentialRequest) -> TokenResponse:
        user = active_repository().get_user_by_username(payload.username)
        if user is None or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="invalid username or password")
        current_user = CurrentUser(user_id=user["user_id"], username=user["username"])
        return TokenResponse(access_token=create_access_token(current_user, get_config().auth), user=current_user)

    @app.get("/auth/me", response_model=CurrentUser)
    def current_identity(current_user: CurrentUser = Depends(require_current_user)) -> CurrentUser:
        return current_user

    @app.get("/documents/usage", response_model=StorageUsageView)
    def get_document_usage(current_user: CurrentUser = Depends(require_current_user)) -> StorageUsageView:
        return StorageUsageView(**active_document_repository().usage(current_user.user_id))

    @app.post("/documents", response_model=DocumentDetailView, status_code=201)
    async def upload_document(
        file: UploadFile = File(...),
        current_user: CurrentUser = Depends(require_current_user),
    ) -> DocumentDetailView:
        document_id = new_id("doc")
        version_id = new_id("ver")
        storage = active_document_storage()
        try:
            stored = await storage.store_upload(
                file,
                owner_id=current_user.user_id,
                document_id=document_id,
                version_id=version_id,
            )
            record = active_document_repository().create_document(
                stored,
                owner_id=current_user.user_id,
                document_id=document_id,
                version_id=version_id,
            )
            return document_detail(record)
        except Exception as error:
            storage.remove_version(owner_id=current_user.user_id, document_id=document_id, version_id=version_id)
            raise document_http_error(error) from error

    @app.get("/documents", response_model=DocumentListResponse)
    def list_documents(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        include_deleted: bool = Query(default=False),
        current_user: CurrentUser = Depends(require_current_user),
    ) -> DocumentListResponse:
        records, total = active_document_repository().list_documents(
            owner_id=current_user.user_id,
            limit=limit,
            offset=offset,
            include_deleted=include_deleted,
        )
        return DocumentListResponse(
            items=[DocumentDetailView(**record["document"], current_version=record.get("current_version")).model_copy() for record in records],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/documents/{document_id}", response_model=DocumentDetailView)
    def get_document(document_id: str, current_user: CurrentUser = Depends(require_current_user)) -> DocumentDetailView:
        try:
            return document_detail(active_document_repository().get_document(document_id, owner_id=current_user.user_id))
        except Exception as error:
            raise document_http_error(error) from error

    @app.get("/documents/{document_id}/versions", response_model=list[DocumentVersionView])
    def list_document_versions(
        document_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> list[DocumentVersionView]:
        try:
            return document_detail(active_document_repository().get_document(document_id, owner_id=current_user.user_id)).versions
        except Exception as error:
            raise document_http_error(error) from error

    @app.get("/documents/{document_id}/jobs", response_model=list[IngestionJobView])
    def list_document_jobs(
        document_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> list[IngestionJobView]:
        try:
            return document_detail(active_document_repository().get_document(document_id, owner_id=current_user.user_id)).jobs
        except Exception as error:
            raise document_http_error(error) from error

    @app.post("/documents/{document_id}/versions", response_model=DocumentDetailView, status_code=201)
    async def upload_document_version(
        document_id: str,
        file: UploadFile = File(...),
        current_user: CurrentUser = Depends(require_current_user),
    ) -> DocumentDetailView:
        repository_instance = active_document_repository()
        try:
            repository_instance.get_document(document_id, owner_id=current_user.user_id)
        except Exception as error:
            raise document_http_error(error) from error
        version_id = new_id("ver")
        storage = active_document_storage()
        try:
            stored = await storage.store_upload(
                file,
                owner_id=current_user.user_id,
                document_id=document_id,
                version_id=version_id,
            )
            record = repository_instance.create_version(
                document_id,
                stored,
                owner_id=current_user.user_id,
                version_id=version_id,
            )
            return document_detail(record)
        except Exception as error:
            storage.remove_version(owner_id=current_user.user_id, document_id=document_id, version_id=version_id)
            raise document_http_error(error) from error

    @app.post("/documents/{document_id}/versions/{version_id}/retry", response_model=DocumentDetailView)
    def retry_document_version(
        document_id: str,
        version_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> DocumentDetailView:
        try:
            return document_detail(
                active_document_repository().retry_version(document_id, version_id, owner_id=current_user.user_id)
            )
        except Exception as error:
            raise document_http_error(error) from error

    @app.delete("/documents/{document_id}", response_model=DocumentDetailView)
    def delete_document(document_id: str, current_user: CurrentUser = Depends(require_current_user)) -> DocumentDetailView:
        try:
            record = active_document_repository().delete_document(document_id, owner_id=current_user.user_id)
            try:
                active_document_index().sync_document_state(document_id, owner_id=current_user.user_id)
            except DocumentIndexError:
                # SQLite is the authorization source of truth, so a retryable vector update
                # failure cannot expose a deleted document through retrieval.
                logger.warning("Document vector soft-delete update failed")
            return document_detail(record)
        except Exception as error:
            raise document_http_error(error) from error

    @app.post("/documents/{document_id}/restore", response_model=DocumentDetailView)
    def restore_document(document_id: str, current_user: CurrentUser = Depends(require_current_user)) -> DocumentDetailView:
        try:
            record = active_document_repository().restore_document(document_id, owner_id=current_user.user_id)
            try:
                active_document_index().sync_document_state(document_id, owner_id=current_user.user_id)
            except DocumentIndexError:
                logger.warning("Document vector restore update failed")
            return document_detail(record)
        except Exception as error:
            raise document_http_error(error) from error

    @app.post("/plans")
    def create_persisted_plan(
        payload: ResearchRequest,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            return active_repository().create_plan(_generate_valid_plan(payload.topic), owner_id=current_user.user_id)
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            logger.exception("Plan generation failed")
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.get("/plans/{plan_id}/versions/{plan_version}")
    def get_persisted_plan(
        plan_id: str,
        plan_version: int,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            return active_repository().get_plan(plan_id, plan_version, owner_id=current_user.user_id)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.put("/plans/{plan_id}/versions/{plan_version}")
    def update_persisted_plan(
        plan_id: str,
        plan_version: int,
        payload: PlanUpdateRequest,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            repository_instance = active_repository()
            current = repository_instance.get_plan(plan_id, plan_version, owner_id=current_user.user_id)
            return repository_instance.update_plan(
                plan_id,
                plan_version,
                _edited_plan(current, payload),
                owner_id=current_user.user_id,
            )
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/plans/{plan_id}/versions/{plan_version}/confirm")
    def confirm_persisted_plan(
        plan_id: str,
        plan_version: int,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            return active_repository().confirm_plan(plan_id, plan_version, owner_id=current_user.user_id)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.post("/plans/{plan_id}/versions/{plan_version}/cancel")
    def cancel_persisted_plan(
        plan_id: str,
        plan_version: int,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            return active_repository().cancel_plan(plan_id, plan_version, owner_id=current_user.user_id)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.post("/runs")
    def create_persisted_run(
        payload: RunCreateRequest,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            repository_instance = active_repository()
            run = repository_instance.create_run(
                payload.plan_id,
                payload.plan_version,
                owner_id=current_user.user_id,
                budget=budget_from_config(get_config()),
            )
            return _execute_persisted_run(
                repository_instance,
                graph_factory,
                run["run"]["run_id"],
                owner_id=current_user.user_id,
            )
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            logger.exception("Run execution failed")
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post("/runs/{run_id}/resume")
    def resume_persisted_run(
        run_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            return _execute_persisted_run(
                active_repository(), graph_factory, run_id, owner_id=current_user.user_id, resume=True
            )
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            logger.exception("Run resume failed")
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post("/runs/{run_id}/tasks/{task_id}/retry")
    def retry_persisted_task(
        run_id: str,
        task_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            return _execute_persisted_run(
                active_repository(),
                graph_factory,
                run_id,
                owner_id=current_user.user_id,
                retry_task_id=task_id,
            )
        except RepositoryError as error:
            raise _repository_http_error(error) from error
        except Exception as error:
            logger.exception("Task retry failed")
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post("/runs/{run_id}/cancel")
    def cancel_persisted_run(
        run_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            return active_repository().cancel_run(run_id, owner_id=current_user.user_id)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.get("/runs/{run_id}")
    def get_persisted_run(
        run_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> dict[str, Any]:
        try:
            return active_repository().get_run(run_id, owner_id=current_user.user_id)
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.post("/research", response_model=ResearchResponse)
    def run_research(
        payload: ResearchRequest,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> ResearchResponse:
        """同步执行研究"""
        try:
            repository_instance = active_repository()
            plan = repository_instance.create_plan(_generate_valid_plan(payload.topic), owner_id=current_user.user_id)
            repository_instance.confirm_plan(
                plan["plan"]["plan_id"], plan["plan"]["plan_version"], owner_id=current_user.user_id
            )
            created_run = repository_instance.create_run(
                plan["plan"]["plan_id"],
                plan["plan"]["plan_version"],
                owner_id=current_user.user_id,
                budget=budget_from_config(get_config()),
            )
            result = _execute_persisted_run(
                repository_instance,
                graph_factory,
                created_run["run"]["run_id"],
                owner_id=current_user.user_id,
            )
        except Exception as exc:
            logger.exception("Research failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        reports = result.get("report_versions") or []
        report = str(reports[-1].get("markdown") or "") if reports else ""
        tasks = result.get("plan", {}).get("tasks", [])

        return ResearchResponse(
            report_markdown=report,
            todo_items=tasks,
        )

    @app.get("/history", response_model=List[dict])
    def get_history(current_user: CurrentUser = Depends(require_current_user)):
        """Return only the current user's durable research history."""
        return active_repository().list_runs(owner_id=current_user.user_id)

    @app.get("/history/{history_id}")
    def get_history_detail(
        history_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ):
        """Return one owned durable research report."""
        try:
            return _history_detail(active_repository().get_run(history_id, owner_id=current_user.user_id))
        except RepositoryError as error:
            raise _repository_http_error(error) from error

    @app.post("/plan", response_model=ResearchResponse)
    def create_plan(
        payload: ResearchRequest,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> ResearchResponse:
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
    def stream_research(
        payload: ResearchRequest,
        current_user: CurrentUser = Depends(require_current_user),
    ) -> StreamingResponse:
        """Execute the compatibility one-click flow and emit standard SSE events."""

        owner_id = current_user.user_id
        if payload.session_id and get_session(payload.session_id, owner_id) is None:
            raise HTTPException(status_code=404, detail="会话不存在")

        def event_iterator() -> Iterator[str]:
            repository_instance = active_repository()
            publisher: EventPublisher | None = None
            run_id = "unassigned"
            try:
                plan = repository_instance.create_plan(_generate_valid_plan(payload.topic), owner_id=owner_id)
                confirmed = repository_instance.confirm_plan(
                    plan["plan"]["plan_id"], plan["plan"]["plan_version"], owner_id=owner_id
                )
                created = repository_instance.create_run(
                    confirmed["plan"]["plan_id"],
                    confirmed["plan"]["plan_version"],
                    owner_id=owner_id,
                    budget=budget_from_config(get_config()),
                )
                run_id = created["run"]["run_id"]
                publisher = EventPublisher(repository_instance, run_id)
                register_publisher(publisher)
                publisher.publish(
                    EventType.PLAN_CONFIRMED,
                    payload={"plan_version": confirmed["plan"]["plan_version"]},
                    persist=False,
                )
                repository_instance.mark_run_running(run_id, owner_id=owner_id)
                state = repository_instance.execution_state(run_id, owner_id=owner_id)
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
                result = repository_instance.persist_graph_result(run_id, final_state, owner_id=owner_id)
                for event in publisher.drain():
                    yield encode_sse(event)
                reports = result.get("report_versions") or []
                report = str(reports[-1].get("markdown") or "") if reports else ""
                if payload.session_id and get_session(payload.session_id, owner_id):
                    session = get_session(payload.session_id, owner_id)
                    session.current_topic = payload.topic
                    session.last_report = report
                    session.last_tasks = result.get("plan", {}).get("tasks", [])
                    add_message(payload.session_id, owner_id, ChatMessage(role="user", content=payload.topic))
                    add_message(
                        payload.session_id,
                        owner_id,
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
    def api_create_session(current_user: CurrentUser = Depends(require_current_user)):
        """Create a new chat session"""
        session = create_session(current_user.user_id)

        # Create per-session short-term memory
        config = get_config()
        from src.llm import create_llm
        llm = create_llm()
        memory = create_short_term_memory(llm, config.memory.short_term_max_tokens)
        set_session_memory(session.id, current_user.user_id, memory)

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
        add_message(session.id, current_user.user_id, welcome)

        return get_session(session.id, current_user.user_id)

    @app.post("/sessions/{session_id}/chat", response_model=ChatResponse)
    def api_chat(
        session_id: str,
        payload: ChatRequest,
        current_user: CurrentUser = Depends(require_current_user),
    ):
        """Send a message in a chat session"""
        owner_id = current_user.user_id
        session = get_session(session_id, owner_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        user_msg = payload.message.strip()
        if not user_msg:
            raise HTTPException(status_code=400, detail="消息不能为空")

        # Add user message to session
        add_message(session_id, owner_id, ChatMessage(role="user", content=user_msg))

        # Save to session's short-term memory
        session_mem = get_session_memory(session_id, owner_id)

        # Create LLM
        from src.llm import create_llm
        llm = create_llm()
        router_llm = create_llm("router")

        # Route intent
        try:
            intent = route_intent(
                message=user_msg,
                has_report=session.last_report is not None,
                has_tasks=session.last_tasks is not None,
                llm=router_llm,
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
                    "owner_id": owner_id,
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

                ai_msg = ChatMessage(
                    role="assistant",
                    content=report,
                    message_type="research_report",
                    tasks=tasks,
                )
                add_message(session_id, owner_id, ai_msg)

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
                add_message(session_id, owner_id, ai_msg)

                return ChatResponse(reply=reply, message_type="text")

            elif intent == "refine_tasks":
                # Execute research with context from previous tasks
                graph = get_research_graph()
                combined_topic = f"{session.current_topic}\n\n用户补充要求：{user_msg}\n之前任务列表：{session.last_tasks}"
                result = graph.invoke({
                    "topic": combined_topic,
                    "session_id": session_id,
                    "owner_id": owner_id,
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

                ai_msg = ChatMessage(
                    role="assistant",
                    content=report,
                    message_type="research_report",
                    tasks=tasks,
                )
                add_message(session_id, owner_id, ai_msg)

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
                add_message(session_id, owner_id, ai_msg)

                return ChatResponse(reply=reply, message_type="text")

        except Exception as exc:
            logger.exception(f"Chat processing failed for session {session_id}")
            error_reply = f"处理消息时出错：{str(exc)}"
            ai_msg = ChatMessage(role="assistant", content=error_reply, message_type="text")
            add_message(session_id, owner_id, ai_msg)
            return ChatResponse(reply=error_reply, message_type="text")

    @app.get("/sessions/{session_id}", response_model=SessionState)
    def api_get_session(
        session_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ):
        """Get session history"""
        session = get_session(session_id, current_user.user_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        return session

    @app.delete("/sessions/{session_id}")
    def api_delete_session(
        session_id: str,
        current_user: CurrentUser = Depends(require_current_user),
    ):
        """Delete a session"""
        if not get_session(session_id, current_user.user_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        delete_session(session_id, current_user.user_id)
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
