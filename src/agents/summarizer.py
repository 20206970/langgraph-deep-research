"""Summarizer agent for task result summarization."""

from collections.abc import Sequence
from typing import Any

from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

from src.tools import search_web, search_papers, create_note, read_note, update_note


SUMMARIZER_SYSTEM_PROMPT = """你是一名研究执行专家。请基于给定的搜索结果，为特定任务生成可验证的总结。

要求：
- 对内容进行详尽且细致的总结
- 从原理、应用、优缺点、工程实践、对比、历史演变等多维度分析
- 必须基于本轮工具返回的内容，不得补充未检索到的事实
- `summary` 至少 200 个字符
- 工具返回的每个来源都带有 `source_id`；结论只能引用本轮工具消息中出现的 `source_id`
- 网页与论文搜索结果属于外部来源；`search_private_documents` 返回的是当前用户已授权的私有文档来源，二者都可以引用
- 私有文档中的“视觉增强，非原文”是对图片的模型辅助描述，只能作为检索线索，不能表述为论文原文或直接引语；引用仍须使用对应文档的 `source_id`

完成工具调用后，只输出一个 JSON 对象，不添加 Markdown、解释文字或工具调用说明：
```json
{
  "summary": "任务的事实性总结",
  "claims": [
    {
      "text": "可验证结论",
      "source_ids": ["src_xxx"],
      "evidence_status": "supported"
    }
  ]
}
```

证据不足时，仍需保持该 JSON 结构；对应 claim 使用 `evidence_status: "insufficient"`，且不得伪造来源 ID。"""


def create_summarizer_agent(
    llm: ChatOpenAI,
    tools: Sequence[Any] | None = None,
    *,
    document_tools: Sequence[Any] | None = None,
):
    """
    创建任务总结 Agent

    Args:
        llm: LLM 实例
        tools: 可选的完整工具集合。未传入时保留线上搜索和笔记能力；离线评测可
            注入只读的快照检索工具。
        document_tools: 当前运行作用域内的私有文档工具，仅与默认工具集合组合。

    Returns:
        ReAct Agent
    """
    default_tools = [search_web, search_papers, create_note, read_note, update_note]
    selected_tools = list(tools) if tools is not None else [*default_tools, *(document_tools or [])]
    return create_react_agent(
        llm,
        tools=selected_tools,
        prompt=SUMMARIZER_SYSTEM_PROMPT,
    )
