"""Short-term memory using ConversationSummaryBufferMemory"""

from typing import Optional

from langchain_classic.memory import ConversationSummaryBufferMemory
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage


# 全局内存实例缓存
_short_term_memory: Optional[ConversationSummaryBufferMemory] = None


def create_short_term_memory(
    llm: ChatOpenAI,
    max_token_limit: int = 2000,
) -> ConversationSummaryBufferMemory:
    """
    创建短期记忆（对话缓冲 + 自动摘要）

    Args:
        llm: LLM 实例，用于生成摘要
        max_token_limit: 最大 token 数，超过时自动生成摘要

    Returns:
        ConversationSummaryBufferMemory 实例
    """
    memory = ConversationSummaryBufferMemory(
        llm=llm,
        max_token_limit=max_token_limit,
        return_messages=True,
        output_key="output",
        input_key="input",
    )
    return memory


def get_short_term_memory(
    llm: ChatOpenAI,
    max_token_limit: int = 2000,
    force_new: bool = False,
) -> ConversationSummaryBufferMemory:
    """
    获取短期记忆实例（带缓存）

    Args:
        llm: LLM 实例
        max_token_limit: 最大 token 数
        force_new: 强制创建新实例

    Returns:
        ConversationSummaryBufferMemory 实例
    """
    global _short_term_memory

    if _short_term_memory is None or force_new:
        _short_term_memory = create_short_term_memory(llm, max_token_limit)

    return _short_term_memory


def add_to_short_term_memory(
    memory: ConversationSummaryBufferMemory,
    user_input: str,
    agent_output: str,
) -> None:
    """
    添加对话到短期记忆

    Args:
        memory: 内存实例
        user_input: 用户输入
        agent_output: Agent 输出
    """
    memory.save_context(
        {"input": user_input},
        {"output": agent_output}
    )


def get_memory_context(memory: ConversationSummaryBufferMemory) -> str:
    """
    获取格式化后的记忆上下文

    Args:
        memory: 内存实例

    Returns:
        格式化的记忆字符串
    """
    messages = memory.chat_memory.messages
    if not messages:
        return ""

    # 返回最近的对话历史
    recent = messages[-4:]  # 最近 4 条消息
    return "\n".join([
        f"{'用户' if isinstance(m, HumanMessage) else 'AI'}: {m.content}"
        for m in recent
    ])