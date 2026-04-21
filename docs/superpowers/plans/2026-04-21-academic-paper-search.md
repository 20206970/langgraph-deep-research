# Academic Paper Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ArXiv and Semantic Scholar academic paper search as a parallel tool alongside the existing web search.

**Architecture:** Extend `src/tools/search.py` with a new `@tool` function `search_papers` that tries ArXiv first, falls back to Semantic Scholar, and returns JSON results. Wire it into the summarizer agent's tool list alongside `search_web`.

**Tech Stack:** `arxiv` (PyPI), `httpx` (for Semantic Scholar REST API)

---

### Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `arxiv` and `httpx` to dependencies**

In `pyproject.toml`, add two entries to the `dependencies` list:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "langgraph>=0.2.0",
    "langchain>=0.3.0",
    "langchain-openai>=0.2.0",
    "langchain-chroma>=0.1.0",
    "langchain-community>=0.3.0",
    "tavily-python>=0.5.0",
    "python-dotenv>=1.0.1",
    "pydantic>=2.0.0",
    "loguru>=0.7.3",
    "arxiv>=2.1.0",
    "httpx>=0.27.0",
]
```

- [ ] **Step 2: Install new dependencies**

Run: `pip install arxiv httpx`
Expected: Successfully installed

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add arxiv and httpx dependencies for paper search"
```

---

### Task 2: Add PaperResult model and ArXiv search

**Files:**
- Modify: `src/tools/search.py`

Add the `PaperResult` model and the `_search_arxiv` helper function at the end of the file, after the existing `_search_with_wikipedia` function (around line 174) and before the `@tool` decorator of `search_web` (line 229).

- [ ] **Step 1: Add imports at the top of search.py**

Add these imports after the existing import block (after line 8):

```python
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
```

- [ ] **Step 2: Add PaperResult model after SearchResult (after line 34)**

```python
class PaperResult(BaseModel):
    """论文搜索结果结构"""
    title: str
    authors: list[str]
    abstract: str
    url: str
    published_date: Optional[str] = None
    source: str  # "arxiv" | "semantic_scholar"
    citation_count: Optional[int] = None
```

- [ ] **Step 3: Add _search_arxiv function (insert before the @tool decorator of search_web, i.e. before line 229)**

```python
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
```

- [ ] **Step 4: Commit**

```bash
git add src/tools/search.py
git commit -m "feat: add PaperResult model and ArXiv search helper"
```

---

### Task 3: Add Semantic Scholar search

**Files:**
- Modify: `src/tools/search.py`

Add `_search_semantic_scholar` function right after `_search_arxiv`.

- [ ] **Step 1: Add _search_semantic_scholar function**

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add src/tools/search.py
git commit -m "feat: add Semantic Scholar search helper"
```

---

### Task 4: Add search_papers tool function

**Files:**
- Modify: `src/tools/search.py`

Add the `@tool` decorated `search_papers` function after `_search_semantic_scholar` and before the existing `search_web` `@tool` function.

- [ ] **Step 1: Add search_papers tool**

```python
@tool
def search_papers(query: str, max_results: int = 5) -> str:
    """
    搜索学术论文，覆盖 ArXiv 和 Semantic Scholar 数据库。
    适用于查找研究论文、学术文献、技术报告等。
    当查询涉及学术研究、论文、算法原理等主题时优先使用此工具。

    Args:
        query: 搜索查询关键词
        max_results: 返回结果数量，默认5条

    Returns:
        JSON 格式的论文搜索结果，包含标题、作者、摘要、URL、发表日期等
    """
    # 首先尝试 ArXiv
    arxiv_result = _search_arxiv(query, max_results)
    if arxiv_result.get("results"):
        output = {
            "results": arxiv_result["results"],
            "source": arxiv_result.get("source", "arxiv"),
        }
        return json.dumps(output, ensure_ascii=False)

    # 降级到 Semantic Scholar
    ss_result = _search_semantic_scholar(query, max_results)
    if ss_result.get("results"):
        output = {
            "results": ss_result["results"],
            "source": ss_result.get("source", "semantic_scholar"),
        }
        return json.dumps(output, ensure_ascii=False)

    # 所有方案都失败
    return json.dumps({
        "results": [],
        "note": f"未找到与 '{query}' 相关的学术论文"
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/tools/search.py
git commit -m "feat: add search_papers tool with ArXiv and Semantic Scholar"
```

---

### Task 5: Export search_papers and wire into summarizer agent

**Files:**
- Modify: `src/tools/__init__.py`
- Modify: `src/agents/summarizer.py`

- [ ] **Step 1: Export search_papers in __init__.py**

Change `src/tools/__init__.py` to:

```python
"""Tools for LangGraph Deep Research"""
from .search import search_web, search_papers
from .notes import create_note, read_note, update_note, delete_note, search_notes

__all__ = [
    "search_web",
    "search_papers",
    "create_note",
    "read_note",
    "update_note",
    "delete_note",
    "search_notes",
]
```

- [ ] **Step 2: Add search_papers to summarizer agent tools**

In `src/agents/summarizer.py`, update the import and tools list:

Change the import line:
```python
from src.tools import search_web, create_note, read_note, update_note
```
to:
```python
from src.tools import search_web, search_papers, create_note, read_note, update_note
```

Change the tools list in `create_summarizer_agent`:
```python
tools=[search_web, create_note, read_note, update_note],
```
to:
```python
tools=[search_web, search_papers, create_note, read_note, update_note],
```

- [ ] **Step 3: Commit**

```bash
git add src/tools/__init__.py src/agents/summarizer.py
git commit -m "feat: wire search_papers into summarizer agent tools"
```

---

### Task 6: Update .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add comment about paper search**

In `.env.example`, after the `TAVILY_API_KEY` line, add:

```
# Paper Search (built-in, no API key needed)
# Supports ArXiv and Semantic Scholar
```

So the relevant section becomes:
```
# Search API
TAVILY_API_KEY=tvly-xxx

# Paper Search (built-in, no API key needed)
# Supports ArXiv and Semantic Scholar

# Memory Configuration
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add paper search note to .env.example"
```

---

### Task 7: Smoke test

- [ ] **Step 1: Verify imports work**

Run: `python -c "from src.tools import search_papers; print('OK')"`
Expected: `OK`

- [ ] **Step 2: Verify tool is properly decorated**

Run: `python -c "from src.tools.search import search_papers; print(search_papers.name, search_papers.description[:50])"`
Expected: prints tool name and first 50 chars of description

- [ ] **Step 3: Quick integration check**

Run: `python -c "from src.agents.summarizer import create_summarizer_agent; print('OK')"`
Expected: `OK` (may warn about missing API key but should not error on import)
