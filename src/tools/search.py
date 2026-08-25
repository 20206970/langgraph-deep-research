"""Search tool using Tavily with DuckDuckGo fallback"""

import json
import re
from functools import lru_cache
from datetime import datetime, timezone
from hashlib import sha256
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from langchain_core.tools import tool
from pydantic import BaseModel

from src.cache import SQLiteSearchCache, build_cache_key

# 尝试导入 Tavily，如果失败则跳过
try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False

# 尝试导入 DuckDuckGo (新包名为 ddgs)
try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False

# 尝试导入 arxiv
try:
    import arxiv
    ARXIV_AVAILABLE = True
except ImportError:
    ARXIV_AVAILABLE = False

# 尝试导入 httpx
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class SearchResult(BaseModel):
    """搜索结果结构"""
    title: str
    url: str
    content: str
    answer: Optional[str] = None


class PaperResult(BaseModel):
    """论文搜索结果结构"""
    title: str
    authors: list[str]
    abstract: str
    url: str
    published_date: Optional[str] = None
    source: str  # "arxiv" | "semantic_scholar"
    citation_count: Optional[int] = None


_TRACKING_QUERY_KEYS = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref"}


def canonicalize_url(url: str | None) -> str | None:
    """Remove fragments and common tracking parameters from a source URL."""
    if not url or not str(url).strip():
        return None

    parsed = urlsplit(str(url).strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            urlencode(sorted(query_pairs)),
            "",
        )
    )


def normalize_sources(
    raw_results: list[dict],
    provider: str,
    source_type: str,
) -> list[dict]:
    """Normalize external search results into bounded, traceable source snapshots."""
    normalized = []
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "无标题").strip()[:500]
        evidence_excerpt = str(raw.get("content") or raw.get("abstract") or title).strip()[:1_500]
        canonical_url = canonicalize_url(raw.get("url"))
        content_hash = sha256(evidence_excerpt.encode("utf-8")).hexdigest()
        identity = f"{canonical_url or provider}|{content_hash}"
        source_id = f"src_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"
        normalized.append(
            {
                "source_id": source_id,
                "provider": provider,
                "source_type": source_type,
                "title": title,
                "url": canonical_url,
                "canonical_url": canonical_url,
                "content": evidence_excerpt,
                "evidence_excerpt": evidence_excerpt,
                "content_hash": content_hash,
                "retrieved_at": retrieved_at,
            }
        )
    return normalized


def _tool_output(result: dict, provider: str, source_type: str) -> str:
    """Serialize standardized sources without retaining complete remote documents."""
    output = {
        "answer": result.get("answer"),
        "provider": provider,
        "results": normalize_sources(result.get("results", []), provider, source_type),
    }
    if result.get("note"):
        output["note"] = result["note"]
    return json.dumps(output, ensure_ascii=False)


@lru_cache(maxsize=1)
def _live_search_cache() -> SQLiteSearchCache | None:
    """Build one local cache handle; callers treat failures as cache misses."""
    try:
        from src.config import get_config

        config = get_config()
        if not config.search_cache.enabled:
            return None
        return SQLiteSearchCache(config.storage.sqlite_path, config.search_cache.ttl_seconds)
    except Exception:
        return None


def _cache_metadata(payload: dict, *, cache_hit: bool, created_at: str | None = None, expires_at: str | None = None) -> str:
    """Attach cache provenance without altering the normalized source snapshots."""
    result = dict(payload)
    result["cache_hit"] = cache_hit
    if created_at:
        result["cached_at"] = created_at
    if expires_at:
        result["expires_at"] = expires_at
    return json.dumps(result, ensure_ascii=False)


def _cached_tool_output(
    tool_name: str,
    query: str,
    max_results: int,
    provider_config: dict,
    producer,
) -> str:
    """Read/write normalized live-search JSON and safely fall back on cache errors."""
    try:
        from src.config import get_config

        config = get_config()
        cache = _live_search_cache()
        if cache is None:
            return _cache_metadata(json.loads(producer()), cache_hit=False)
        cache_key = build_cache_key(
            tool_name=tool_name,
            query=query,
            provider_config=provider_config,
            language=config.search_cache.language,
            max_results=max_results,
            tool_version=config.search_cache.tool_version,
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return _cache_metadata(
                cached.payload,
                cache_hit=True,
                created_at=cached.created_at,
                expires_at=cached.expires_at,
            )
        payload = json.loads(producer())
        if isinstance(payload, dict) and payload.get("results"):
            entry = cache.put(cache_key, payload)
            return _cache_metadata(payload, cache_hit=False, created_at=entry.created_at, expires_at=entry.expires_at)
        return _cache_metadata(payload if isinstance(payload, dict) else {}, cache_hit=False)
    except Exception:
        # A cache fault must remain observable as a miss, while live search keeps working.
        try:
            payload = json.loads(producer())
        except Exception:
            return producer()
        return _cache_metadata(payload if isinstance(payload, dict) else {}, cache_hit=False)


def _generate_fallback_queries(query: str) -> list[str]:
    """生成备用搜索查询关键词"""
    fallbacks = [query]

    # 提取关键技术术语，尝试更通用的搜索
    if "quantum" in query.lower() or "量子" in query:
        fallbacks.append("quantum machine learning algorithms")
        fallbacks.append("quantum computing AI applications")
    if "graph" in query.lower() or "图" in query:
        fallbacks.append("graph neural networks GNN")
        fallbacks.append("deep learning graphs")
    if "biolog" in query.lower() or "生物" in query:
        fallbacks.append("machine learning bioinformatics")
        fallbacks.append("deep learning biology")

    # 提取英文关键词
    english_words = re.findall(r'[a-zA-Z]{3,}', query)
    if english_words:
        fallbacks.append(" ".join(english_words[:3]))

    return fallbacks


def _search_with_duckduckgo(query: str, max_results: int = 5) -> dict:
    """
    使用 DuckDuckGo 进行搜索（免费降级方案）

    Args:
        query: 搜索查询
        max_results: 最大结果数

    Returns:
        搜索结果字典
    """
    if not DDGS_AVAILABLE:
        return {"answer": None, "results": [], "error": "duckduckgo-search not installed"}

    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=max_results))

        search_results = []
        for item in results:
            # DuckDuckGo 返回的结果包含 title, href, body
            content = item.get("body", "")
            if content and len(content) > 50:
                search_results.append({
                    "title": item.get("title", "无标题"),
                    "url": item.get("href", ""),
                    "content": content[:1000],
                })

        if search_results:
            return {
                "answer": None,
                "results": search_results,
                "source": "duckduckgo",
            }
        else:
            return {"answer": None, "results": [], "error": "no results from duckduckgo"}

    except Exception as e:
        return {"answer": None, "results": [], "error": str(e)}


def _search_with_wikipedia(query: str, max_results: int = 5) -> dict:
    """
    使用 Wikipedia API 进行搜索（免费降级方案）

    Args:
        query: 搜索查询
        max_results: 最大结果数

    Returns:
        搜索结果字典
    """
    import urllib.parse
    import urllib.request

    try:
        # 搜索 Wikipedia
        search_url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": max_results,
        }
        encoded_params = urllib.parse.urlencode(params)
        url = f"{search_url}?{encoded_params}"

        req = urllib.request.Request(url, headers={"User-Agent": "LangGraph-Research/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        search_results = data.get("query", {}).get("search", [])

        if not search_results:
            return {"answer": None, "results": [], "error": "no wikipedia results"}

        # 获取每个页面的摘要
        result_list = []
        for item in search_results[:max_results]:
            page_id = item.get("pageid")
            # 获取页面摘要
            summary_params = {
                "action": "query",
                "pageids": page_id,
                "prop": "extracts",
                "exintro": True,
                "explaintext": True,
                "format": "json",
            }
            summary_url = f"{search_url}?{urllib.parse.urlencode(summary_params)}"
            summary_req = urllib.request.Request(summary_url, headers={"User-Agent": "LangGraph-Research/1.0"})

            with urllib.request.urlopen(summary_req, timeout=10) as summary_response:
                summary_data = json.loads(summary_response.read().decode("utf-8"))

            pages = summary_data.get("query", {}).get("pages", {})
            page_data = pages.get(str(page_id), {})
            extract = page_data.get("extract", "")

            result_list.append({
                "title": item.get("title", "无标题"),
                "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(item.get('title', '').replace(' ', '_'))}",
                "content": extract[:1000] if extract else item.get("snippet", ""),
            })

        return {
            "answer": None,
            "results": result_list,
            "source": "wikipedia",
        }

    except Exception as e:
        return {"answer": None, "results": [], "error": str(e)}


def _search_with_tavily(query: str, max_results: int, api_key: str) -> dict:
    """
    使用 Tavily 进行搜索

    Args:
        query: 搜索查询
        max_results: 最大结果数
        api_key: API 密钥

    Returns:
        搜索结果字典
    """
    if not TAVILY_AVAILABLE:
        return {"answer": None, "results": [], "error": "tavily not installed"}

    try:
        client = TavilyClient(api_key=api_key)
        results = client.search(
            query=query,
            max_results=max_results,
            include_answer=True,
            include_raw_content=True,
        )

        # 检查是否有有效结果
        search_results = []
        for item in results.get("results", []):
            content = item.get("content", "")
            if content and len(content) > 100:  # 过滤掉内容过短的结果
                search_results.append({
                    "title": item.get("title", "无标题"),
                    "url": item.get("url", ""),
                    "content": content[:1000],
                })

        if search_results:
            return {
                "answer": results.get("answer"),
                "results": search_results,
                "source": "tavily",
            }

        return {"answer": None, "results": [], "error": "no valid results"}

    except Exception as e:
        error_msg = str(e)
        # 检查是否是配额超限
        if "usage limit" in error_msg.lower() or "quota" in error_msg.lower():
            return {"answer": None, "results": [], "error": "quota_exceeded", "fatal": True}
        return {"answer": None, "results": [], "error": error_msg}


def _search_arxiv(query: str, max_results: int = 5) -> dict:
    """
    使用 ArXiv API 搜索学术论文

    Args:
        query: 搜索查询
        max_results: 最大结果数

    Returns:
        搜索结果字典
    """
    if not ARXIV_AVAILABLE:
        return {"results": [], "error": "arxiv package not installed"}

    try:
        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.Relevance,
        )

        results = []
        for paper in client.results(search):
            authors = [a.name for a in paper.authors]
            results.append(PaperResult(
                title=paper.title,
                authors=authors,
                abstract=paper.summary[:500] if paper.summary else "",
                url=paper.entry_id,
                published_date=paper.published.strftime("%Y-%m-%d") if paper.published else None,
                source="arxiv",
            ))

        if results:
            return {"results": [r.model_dump() for r in results], "source": "arxiv"}
        return {"results": [], "error": "no results from arxiv"}

    except Exception as e:
        return {"results": [], "error": str(e)}


def _search_semantic_scholar(query: str, max_results: int = 5) -> dict:
    """
    使用 Semantic Scholar API 搜索学术论文

    Args:
        query: 搜索查询
        max_results: 最大结果数

    Returns:
        搜索结果字典
    """
    if not HTTPX_AVAILABLE:
        return {"results": [], "error": "httpx not installed"}

    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "limit": max_results,
            "fields": "title,authors,abstract,url,year,citationCount,externalIds",
        }

        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        papers = data.get("data", [])
        results = []
        for paper in papers:
            authors = [a.get("name", "") for a in paper.get("authors", [])]
            abstract = paper.get("abstract", "") or ""
            external_ids = paper.get("externalIds", {}) or {}
            paper_url = paper.get("url", "")

            # 优先使用 ArXiv 链接
            if external_ids.get("ArXiv"):
                paper_url = f"https://arxiv.org/abs/{external_ids['ArXiv']}"

            results.append(PaperResult(
                title=paper.get("title", "无标题"),
                authors=authors,
                abstract=abstract[:500],
                url=paper_url,
                published_date=str(paper["year"]) if paper.get("year") else None,
                source="semantic_scholar",
                citation_count=paper.get("citationCount"),
            ))

        if results:
            return {"results": [r.model_dump() for r in results], "source": "semantic_scholar"}
        return {"results": [], "error": "no results from semantic scholar"}

    except Exception as e:
        return {"results": [], "error": str(e)}


def _search_papers_live(query: str, max_results: int = 5) -> str:
    """
    搜索学术论文，覆盖 ArXiv 和 Semantic Scholar 数据库。
    适用于查找研究论文、学术文献、技术报告等。
    当查询涉及学术研究、论文、算法原理等主题时优先使用此工具。

    Args:
        query: 搜索查询关键词
        max_results: 返回结果数量，默认5条

    Returns:
        JSON 格式的标准化来源，包含 source_id、URL、证据摘要和抓取时间
    """
    # 首先尝试 ArXiv
    arxiv_result = _search_arxiv(query, max_results)
    if arxiv_result.get("results"):
        return _tool_output(arxiv_result, arxiv_result.get("source", "arxiv"), "paper")

    # 降级到 Semantic Scholar
    ss_result = _search_semantic_scholar(query, max_results)
    if ss_result.get("results"):
        return _tool_output(ss_result, ss_result.get("source", "semantic_scholar"), "paper")

    # 所有方案都失败
    return _tool_output(
        {"results": [], "note": f"未找到与 '{query}' 相关的学术论文"},
        "unknown",
        "paper",
    )


@tool
def search_papers(query: str, max_results: int = 5) -> str:
    """Search academic sources with a TTL cache around normalized source snapshots."""
    return _cached_tool_output(
        "search_papers",
        query,
        max_results,
        {"providers": ["arxiv", "semantic_scholar"], "arxiv_available": ARXIV_AVAILABLE, "httpx_available": HTTPX_AVAILABLE},
        lambda: _search_papers_live(query, max_results),
    )


def _search_web_live(query: str, max_results: int = 5) -> str:
    """Execute the original live web search/fallback flow without cache handling."""
    # 获取 API 配置
    api_key = None
    try:
        from src.config import get_config
        config = get_config()
        api_key = config.search.tavily_api_key
    except Exception:
        pass

    # 首先尝试使用 Tavily（带备用查询）
    if api_key:
        queries_to_try = _generate_fallback_queries(query)
        quota_exceeded = False

        for q in queries_to_try:
            result = _search_with_tavily(q, max_results, api_key)

            # 检查是否是配额超限错误
            if result.get("fatal"):
                quota_exceeded = True
                break

            # 如果有有效结果就返回
            if result.get("results"):
                return _tool_output(result, result.get("source", "tavily"), "web")

        # 如果配额超限，切换到 DuckDuckGo
        if quota_exceeded:
            print(f"  [降级] Tavily 配额超限，切换到 DuckDuckGo")

    # 降级方案 1：使用 DuckDuckGo
    ddg_result = _search_with_duckduckgo(query, max_results)

    if ddg_result.get("results"):
        return _tool_output(ddg_result, ddg_result.get("source", "duckduckgo"), "web")

    # 降级方案 2：使用 Wikipedia
    print(f"  [降级] DuckDuckGo 无结果，切换到 Wikipedia")
    wiki_result = _search_with_wikipedia(query, max_results)

    if wiki_result.get("results"):
        return _tool_output(wiki_result, wiki_result.get("source", "wikipedia"), "web")

    # 所有方案都失败
    return _tool_output(
        {"answer": None, "results": [], "note": f"未找到与 '{query}' 相关的搜索结果"},
        "unknown",
        "web",
    )


@tool
def search_web(query: str, max_results: int = 5) -> str:
    """
    执行网络搜索，返回相关网页内容和摘要。
    使用 Tavily 作为主搜索，当失败时自动降级到 DuckDuckGo 和 Wikipedia。

    Args:
        query: 搜索查询关键词
        max_results: 返回结果数量，默认5条

    Returns:
        JSON 格式的标准化来源，包含 source_id、URL、证据摘要和抓取时间
    """
    try:
        from src.config import get_config

        config = get_config()
        provider_config = {
            "primary": config.search.api,
            "tavily_configured": bool(config.search.tavily_api_key),
            "ddgs_available": DDGS_AVAILABLE,
            "fallback": "wikipedia",
        }
    except Exception:
        provider_config = {"primary": "unknown", "tavily_configured": False, "ddgs_available": DDGS_AVAILABLE}
    return _cached_tool_output(
        "search_web",
        query,
        max_results,
        provider_config,
        lambda: _search_web_live(query, max_results),
    )
