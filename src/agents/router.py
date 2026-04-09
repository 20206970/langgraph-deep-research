"""Intent router for classifying user messages"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


ROUTER_SYSTEM_PROMPT = """你是一个意图分类器。根据用户的最新消息和对话上下文，判断用户的意图。

对话上下文信息：
- 是否有之前的研究报告：{has_report}
- 是否有之前的任务列表：{has_tasks}

请只输出以下四个类别之一，不要输出任何其他内容：

1. new_research - 用户提出了一个新的研究主题或问题，需要执行完整的研究流程
2. follow_up - 用户在追问之前研究报告中的内容，或对之前的研究提出进一步的问题
3. refine_tasks - 用户要求修改、增加或调整之前的研究任务
4. general - 一般性闲聊或简单问题

判断规则：
- 如果之前没有研究报告，用户的输入通常属于 new_research
- 如果之前有研究报告，且用户的问题涉及之前的内容（如"重点讲讲"、"详细说明"、"和之前对比"），属于 follow_up
- 如果用户明确要求修改任务（如"再加一个"、"把第二个换成"），属于 refine_tasks
- 如果是简单的问候或与研究无关的话题，属于 general"""


def route_intent(
    message: str,
    has_report: bool,
    has_tasks: bool,
    llm: ChatOpenAI,
) -> str:
    """
    Classify user message intent.

    Args:
        message: User's latest message
        has_report: Whether session has a previous report
        has_tasks: Whether session has previous tasks
        llm: LLM instance for classification

    Returns:
        One of: "new_research", "follow_up", "refine_tasks", "general"
    """
    system_prompt = ROUTER_SYSTEM_PROMPT.format(
        has_report="是" if has_report else "否",
        has_tasks="是" if has_tasks else "否",
    )

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=message),
    ])

    intent = response.content.strip().lower()

    # Validate and normalize
    valid_intents = {"new_research", "follow_up", "refine_tasks", "general"}
    if intent in valid_intents:
        return intent

    # Fallback: try to extract a valid intent from the response
    for valid in valid_intents:
        if valid in intent:
            return valid

    # Default fallback
    return "new_research"
