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


# 搜索来源结构
class SearchSource(BaseModel):
    """搜索来源"""
    query: str = Field(..., description="搜索查询")
    url: Optional[str] = Field(default=None, description="来源 URL")
    title: Optional[str] = Field(default=None, description="来源标题")


# 使用 Annotated + operator.add 支持并行节点增量写入列表
class ResearchState(dict):
    """研究工作流状态 - 支持并行任务"""
    topic: str = ""
    tasks: list = []
    task_results: Annotated[list, operator.add] = []
    sources: Annotated[list, operator.add] = []  # 存储 SearchSource 对象
    report: Optional[str] = None
    loop_count: int = 0
    memory_context: str = ""
    session_id: Optional[str] = None


# 别名用于类型标注
ResearchStateDict = ResearchState


