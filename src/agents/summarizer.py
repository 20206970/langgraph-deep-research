"""Summarizer agent for task result summarization"""

from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

from src.tools import search_web, search_papers, create_note, read_note, update_note


SUMMARIZER_SYSTEM_PROMPT = """你是一名研究执行专家。请基于给定的搜索结果，为特定任务生成要点总结。

要求：
- 对内容进行详尽且细致的总结
- 从原理、应用、优缺点、工程实践、对比、历史演变等多维度分析
- 使用 Markdown 格式输出

重要：搜索结果中包含来源 URL，你必须在总结最后以 JSON 格式列出参考来源。

输出格式：
- 小节标题："任务总结"
- 关键发现使用有序或无序列表表达
- 总结最后必须包含 JSON 格式的来源列表：
  ```json
  {"sources": [{"title": "来源标题", "url": "https://..."}]}
  ```
- 若任务无有效结果，输出"暂无可用信息"
- 最终呈现给用户的总结中禁止包含工具调用指令"""


def create_summarizer_agent(llm: ChatOpenAI):
    """
    创建任务总结 Agent

    Args:
        llm: LLM 实例

    Returns:
        ReAct Agent
    """
    return create_react_agent(
        llm,
        tools=[search_web, search_papers, create_note, read_note, update_note],
        prompt=SUMMARIZER_SYSTEM_PROMPT,
    )