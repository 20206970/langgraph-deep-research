"""Reporter agent for final report generation"""

from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

from src.tools import create_note, read_note


REPORTER_SYSTEM_PROMPT = """你是一名专业的分析报告撰写者。请根据输入的任务总结与参考信息，生成结构化的研究报告。

报告模板：
1. **背景概览**：简述研究主题的重要性与上下文
2. **核心洞见**：提炼 3-5 条最重要的结论
3. **证据与数据**：罗列支持性的事实或指标
4. **风险与挑战**：分析潜在的问题、限制或仍待验证的假设
5. **参考来源**：按任务列出关键来源条目
6. **结论**：总结研究发现，提出未来研究方向或建议

要求：
- 使用 Markdown 格式
- 各部分明确分节
- 若某部分信息缺失，说明"暂无相关信息"
- 只能使用输入中给出的任务结果和来源；对任务失败或证据不足的内容必须明确说明，不能补写为确定性结论
- 每个关键结论都应附带输入中给出的来源 Markdown 链接；不要创建新的 URL 或来源 ID
- 输出给用户的内容中禁止残留工具调用指令"""


def create_reporter_agent(llm: ChatOpenAI):
    """
    创建报告生成 Agent

    Args:
        llm: LLM 实例

    Returns:
        ReAct Agent
    """
    return create_react_agent(
        llm,
        tools=[create_note, read_note],
        prompt=REPORTER_SYSTEM_PROMPT,
    )
