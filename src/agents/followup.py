"""Follow-up question handler using memory context"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from src.memory.long_term import search_long_term_memory, get_long_term_memory
from src.memory.short_term import get_memory_context


FOLLOWUP_SYSTEM_PROMPT = """你是一个专业的研究助手。用户正在针对之前的研究报告进行追问。

之前的对话上下文：
{conversation_context}

之前的完整研究报告：
{last_report}

相关的长期记忆（之前研究过的相关主题）：
{long_term_context}

请基于以上信息回答用户的追问。要求：
- 引用报告中已有的具体内容
- 如果用户要求深入某个方面，提供更详细的分析
- 如果需要补充新信息，明确指出这是新增内容
- 使用 Markdown 格式
- 保持专业和准确"""


def handle_followup(
    message: str,
    session_state,
    session_memory,
    llm: ChatOpenAI,
) -> str:
    """
    Answer a follow-up question using conversation context and memory.

    Args:
        message: User's follow-up question
        session_state: Current session state (has last_report, etc.)
        session_memory: Session's short-term memory instance
        llm: LLM instance

    Returns:
        Answer text in Markdown
    """
    # Get conversation context from short-term memory
    conversation_context = ""
    if session_memory is not None:
        try:
            conversation_context = get_memory_context(session_memory)
        except Exception:
            pass

    # Get the last report
    last_report = session_state.last_report or "（暂无之前的报告）"

    # Search long-term memory for related content
    long_term_context = ""
    try:
        long_mem = get_long_term_memory()
        if long_mem:
            results = search_long_term_memory(message, long_mem)
            if results:
                long_term_context = "\n".join(results)
    except Exception:
        pass

    if not long_term_context:
        long_term_context = "（无相关历史研究）"

    system_prompt = FOLLOWUP_SYSTEM_PROMPT.format(
        conversation_context=conversation_context or "（无对话历史）",
        last_report=last_report[:3000],  # Limit report length to avoid token overflow
        long_term_context=long_term_context,
    )

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=message),
    ])

    return response.content


def handle_general(
    message: str,
    session_memory,
    llm: ChatOpenAI,
) -> str:
    """
    Handle general chat messages.

    Args:
        message: User's message
        session_memory: Session's short-term memory instance
        llm: LLM instance

    Returns:
        Reply text
    """
    # Get conversation context if available
    conversation_context = ""
    if session_memory is not None:
        try:
            conversation_context = get_memory_context(session_memory)
        except Exception:
            pass

    context_block = f"\n\n对话上下文：\n{conversation_context}" if conversation_context else ""

    response = llm.invoke([
        SystemMessage(content=f"你是一个友好的研究助手。{context_block}"),
        HumanMessage(content=message),
    ])

    return response.content
