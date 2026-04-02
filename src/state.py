"""LangGraph state definitions"""

import operator
from typing import Annotated, Optional

from pydantic import BaseModel, Field


class TaskItem(BaseModel):
    """单个研究任务"""
    id: int = Field(..., description="任务ID")
    title: str = Field(..., description="任务标题")
    intent: str = Field(..., description="任务意图")
    query: str = Field(..., description="搜索查询")
    status: str = Field(default="pending", description="任务状态")
    summary: Optional[str] = Field(default=None, description="任务总结")
    sources_summary: Optional[str] = Field(default=None, description="来源摘要")


class ResearchState(BaseModel):
    """研究工作流状态"""
    topic: str = Field(default="", description="研究主题")
    tasks: list[TaskItem] = Field(default_factory=list, description="任务列表")
    task_results: list[str] = Field(default_factory=list, description="任务结果")
    sources: list[str] = Field(default_factory=list, description="来源列表")
    report: Optional[str] = Field(default=None, description="最终报告")
    loop_count: int = Field(default=0, description="研究循环计数")
    memory_context: str = Field(default="", description="记忆上下文")


class ResearchStateDict(dict):
    """LangGraph 使用的字典状态"""

    def __init__(self, topic: str = "", **kwargs):
        super().__init__(
            topic=topic,
            tasks=[],
            task_results=[],
            sources=[],
            report=None,
            loop_count=0,
            memory_context="",
            **kwargs
        )

    @property
    def topic(self) -> str:
        return self.get("topic", "")

    @topic.setter
    def topic(self, value: str):
        self["topic"] = value

    @property
    def tasks(self) -> list[TaskItem]:
        return self.get("tasks", [])

    @tasks.setter
    def tasks(self, value: list[TaskItem]):
        self["tasks"] = value

    @property
    def task_results(self) -> list[str]:
        return self.get("task_results", [])

    @task_results.setter
    def task_results(self, value: list[str]):
        self["task_results"] = value

    @property
    def sources(self) -> list[str]:
        return self.get("sources", [])

    @sources.setter
    def sources(self, value: list[str]):
        self["sources"] = value

    @property
    def report(self) -> Optional[str]:
        return self.get("report")

    @report.setter
    def report(self, value: Optional[str]):
        self["report"] = value

    @property
    def loop_count(self) -> int:
        return self.get("loop_count", 0)

    @loop_count.setter
    def loop_count(self, value: int):
        self["loop_count"] = value

    @property
    def memory_context(self) -> str:
        return self.get("memory_context", "")

    @memory_context.setter
    def memory_context(self, value: str):
        self["memory_context"] = value


