"""Long-term memory using ChromaDB + VectorStoreRetrieverMemory"""

import os
from pathlib import Path
from typing import Optional

from langchain_classic.memory import VectorStoreRetrieverMemory
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document


# 全局实例
_long_term_memory: Optional[VectorStoreRetrieverMemory] = None
_vectorstore: Optional[Chroma] = None


def create_long_term_memory(
    persist_directory: str = "./chroma_data",
    k: int = 3,
) -> VectorStoreRetrieverMemory:
    """
    创建长期记忆（向量数据库 + 语义检索）

    Args:
        persist_directory: ChromaDB 持久化目录
        k: 检索时返回的最近记忆数

    Returns:
        VectorStoreRetrieverMemory 实例
    """
    global _long_term_memory, _vectorstore

    # 确保目录存在
    Path(persist_directory).mkdir(parents=True, exist_ok=True)

    # 创建嵌入和向量存储
    embeddings = OpenAIEmbeddings()
    _vectorstore = Chroma(
        persist_directory=persist_directory,
        embedding_function=embeddings,
        collection_name="research_memories",
    )

    # 创建检索器
    retriever = _vectorstore.as_retriever(
        search_kwargs={"k": k}
    )

    # 创建长期记忆
    _long_term_memory = VectorStoreRetrieverMemory(
        retriever=retriever,
        memory_key="chat_history",
        return_messages=True,
    )

    return _long_term_memory


def get_long_term_memory(
    persist_directory: str = "./chroma_data",
    k: int = 3,
    force_new: bool = False,
) -> VectorStoreRetrieverMemory:
    """
    获取长期记忆实例（带缓存）

    Args:
        persist_directory: ChromaDB 持久化目录
        k: 检索时返回的最近记忆数
        force_new: 强制创建新实例

    Returns:
        VectorStoreRetrieverMemory 实例
    """
    global _long_term_memory

    if _long_term_memory is None or force_new:
        return create_long_term_memory(persist_directory, k)

    return _long_term_memory


def search_long_term_memory(
    query: str,
    memory: Optional[VectorStoreRetrieverMemory] = None,
) -> list[str]:
    """
    搜索长期记忆

    Args:
        query: 搜索查询
        memory: 内存实例（可选）

    Returns:
        匹配的记忆列表
    """
    if memory is None:
        memory = _long_term_memory

    if memory is None:
        return []

    # 使用检索器搜索
    docs = memory.retriever.invoke(query)
    return [doc.page_content for doc in docs]


def save_to_long_term_memory(
    content: str,
    memory: Optional[VectorStoreRetrieverMemory] = None,
    metadata: Optional[dict] = None,
) -> None:
    """
    保存内容到长期记忆

    Args:
        content: 要保存的内容
        memory: 内存实例（可选）
        metadata: 元数据（可选）
    """
    if memory is None:
        memory = _long_term_memory

    if memory is None:
        return

    # 创建文档
    doc = Document(
        page_content=content,
        metadata=metadata or {}
    )

    # 保存到向量存储
    memory.save_context(
        {"input": ""},
        {"output": content}
    )


def save_research_memory(
    topic: str,
    task_results: list[str],
    report: str,
    memory: Optional[VectorStoreRetrieverMemory] = None,
) -> None:
    """
    保存研究结果到长期记忆

    Args:
        topic: 研究主题
        task_results: 任务结果列表
        report: 最终报告
        memory: 内存实例
    """
    content = f"""
研究主题: {topic}
任务结果:
{chr(10).join(task_results)}

最终报告:
{report}
"""

    metadata = {
        "topic": topic,
        "type": "research",
    }

    save_to_long_term_memory(content, memory, metadata)