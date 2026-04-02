"""FastAPI entry point for LangGraph Deep Research"""

import json
import sys
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.config import get_config
from src.graph.research import get_research_graph
from src.memory.long_term import create_long_term_memory, search_long_term_memory
from src.memory.short_term import create_short_term_memory, get_short_term_memory

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


class ResearchResponse(BaseModel):
    """研究响应"""
    report_markdown: str = Field(..., description="Markdown-formatted research report")
    todo_items: list[dict[str, Any]] = Field(default_factory=list, description="Task items")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(title="LangGraph Deep Researcher")

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

        # 初始化长期记忆
        create_long_term_memory(
            persist_directory=config.memory.long_term_persist_dir,
            k=config.memory.long_term_k,
        )

        # 初始化短期记忆
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=config.llm.model,
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
        )
        create_short_term_memory(llm, config.memory.short_term_max_tokens)

        logger.info(f"LangGraph Deep Researcher initialized")
        logger.info(f"LLM: {config.llm.model} @ {config.llm.base_url}")
        logger.info(f"ChromaDB: {config.memory.long_term_persist_dir}")

    @app.get("/healthz")
    def health_check() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/research", response_model=ResearchResponse)
    def run_research(payload: ResearchRequest) -> ResearchResponse:
        """同步执行研究"""
        try:
            graph = get_research_graph()
            result = graph.invoke({"topic": payload.topic})
        except Exception as exc:
            logger.exception("Research failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        report = result.get("report", "")
        tasks = result.get("tasks", [])

        return ResearchResponse(
            report_markdown=report,
            todo_items=tasks,
        )

    @app.post("/research/stream")
    def stream_research(payload: ResearchRequest) -> StreamingResponse:
        """流式执行研究"""
        graph = get_research_graph()

        def event_iterator() -> Iterator[str]:
            try:
                for chunk in graph.stream({"topic": payload.topic}):
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            except Exception as exc:
                logger.exception("Streaming research failed")
                error_payload = {"type": "error", "detail": str(exc)}
                yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

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