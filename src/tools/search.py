"""Search tool using Tavily"""

from typing import Any, Dict, Optional

from langchain_core.tools import tool
from tavily import TavilyClient


@tool
def search_web(query: str, max_results: int = 5) -> str:
    """
    执行网络搜索，返回相关网页内容和摘要。

    Args:
        query: 搜索查询关键词
        max_results: 返回结果数量，默认5条

    Returns:
        格式化的搜索结果，包含标题、URL和内容摘要
    """
    api_key = None
    try:
        from src.config import get_config
        config = get_config()
        api_key = config.search.tavily_api_key
    except Exception:
        pass

    if not api_key:
        return "错误: 未配置 TAVILY_API_KEY"

    try:
        client = TavilyClient(api_key=api_key)
        results = client.search(
            query=query,
            max_results=max_results,
            include_answer=True,
            include_raw_content=True,
        )

        if not results.get("results"):
            return f"未找到与 '{query}' 相关的搜索结果"

        output = []
        for i, item in enumerate(results.get("results", []), 1):
            title = item.get("title", "无标题")
            url = item.get("url", "")
            content = item.get("content", "")[:500]
            output.append(f"{i}. {title}\n   URL: {url}\n   内容: {content}...")

        answer = results.get("answer")
        if answer:
            output.insert(0, f"AI 摘要: {answer}\n")

        return "\n\n".join(output)

    except Exception as e:
        return f"搜索失败: {str(e)}"