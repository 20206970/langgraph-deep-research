"""Planner agent for task decomposition"""

from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

from src.tools import search_notes


PLANNER_SYSTEM_PROMPT = """你是一名研究规划专家。请把复杂主题拆解为一组有限、互补的待办任务。

要求：
- 任务数量控制在 5-7 个之间，保持精简
- 任务之间应互补，避免重复
- 每个任务要有明确意图与可执行的检索方向
- 输出须结构化、简明且便于后续协作

请严格按照以下 JSON 格式输出：
```json
{
  "tasks": [
    {
      "title": "任务名称",
      "intent": "任务要解决的核心问题",
      "query": "建议使用的检索关键词"
    }
  ]
}
```

只输出一个 JSON 对象，不添加 Markdown、解释文字或工具调用说明。任务数量必须为 1--7 个。"""


def create_planner_agent(llm: ChatOpenAI):
    """
    创建任务规划 Agent

    Args:
        llm: LLM 实例

    Returns:
        ReAct Agent
    """
    return create_react_agent(
        llm,
        tools=[search_notes],
        prompt=PLANNER_SYSTEM_PROMPT,
    )
