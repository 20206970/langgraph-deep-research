"""Long-term memory using ChromaDB + VectorStoreRetrieverMemory"""

import os
from pathlib import Path
from typing import Optional

from langchain_classic.memory import VectorStoreRetrieverMemory
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings


class DashScopeEmbeddings(Embeddings):
    """阿里云 DashScope embeddings 自定义实现"""

    def __init__(self, api_key: str, model: str = "text-embedding-v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of documents"""
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        embeddings = []
        for text in texts:
            response = client.embeddings.create(
                model=self.model,
                input=text
            )
            embeddings.append(response.data[0].embedding)

        return embeddings

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query"""
        return self.embed_documents([text])[0]


# 全局实例
_long_term_memory: Optional[VectorStoreRetrieverMemory] = None
_vectorstore: Optional[Chroma] = None


def _resolve_embedding_device(device: str) -> str:
    """Resolve the portable default without forcing CUDA in local development."""
    if device != "auto":
        return device

    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def create_embeddings(emb_config) -> Embeddings:
    """Create one configured embedding adapter for an isolated vector collection."""

    provider = emb_config.provider.lower().strip()
    if provider == "huggingface":
        from langchain_huggingface import HuggingFaceEmbeddings

        embeddings = HuggingFaceEmbeddings(
            model_name=emb_config.model,
            model_kwargs={"device": _resolve_embedding_device(emb_config.device)},
            encode_kwargs={
                "batch_size": emb_config.batch_size,
                "normalize_embeddings": emb_config.normalize_embeddings,
            },
        )
        # langchain-huggingface renamed this attribute from client to _client.
        model_client = getattr(embeddings, "client", None) or getattr(embeddings, "_client", None)
        if model_client is None:
            raise RuntimeError("HuggingFaceEmbeddings did not expose a SentenceTransformer client")
        model_client.max_seq_length = emb_config.max_length
        return embeddings
    if provider == "dashscope":
        return DashScopeEmbeddings(
            api_key=emb_config.api_key,
            model=emb_config.model,
        )
    if provider in {"openai", "openai_compatible"}:
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=emb_config.model,
            api_key=emb_config.api_key or None,
            base_url=emb_config.base_url or None,
        )
    raise ValueError(f"Unsupported embeddings provider: {emb_config.provider}")


def _get_embeddings_model() -> Embeddings:
    """Create the configured memory embedding model without silently changing providers."""
    from src.config import get_config

    return create_embeddings(get_config().embeddings)


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
    embeddings = _get_embeddings_model()
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
