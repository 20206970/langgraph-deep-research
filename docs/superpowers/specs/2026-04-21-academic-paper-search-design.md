# 学术论文搜索功能设计

## 背景

项目当前仅支持通用网页搜索（Tavily → DuckDuckGo → Wikipedia），缺少学术论文检索能力。需要新增 ArXiv 和 Semantic Scholar 两个学术搜索引擎，作为独立工具与 `search_web` 并行提供给 Agent 使用。

## 方案

在现有 `src/tools/search.py` 中扩展，新增 `search_papers` 工具函数。

### 数据模型

```python
class PaperResult(BaseModel):
    title: str
    authors: list[str]
    abstract: str
    url: str
    published_date: str | None = None
    source: str  # "arxiv" | "semantic_scholar"
    citation_count: int | None = None
```

### 搜索实现

**ArXiv**：使用 `arxiv` Python 包，无需 API Key。支持关键词、标题、摘要搜索。

**Semantic Scholar**：使用 `httpx` 调用 REST API（`api.semanticscholar.org/graph/v1/paper/search`），无需 API Key。返回标题、摘要、引用数、作者、年份。

降级策略：ArXiv → Semantic Scholar → 空结果。

### 工具函数

```python
@tool
def search_papers(query: str, max_results: int = 5) -> str:
    """搜索学术论文，覆盖 ArXiv 和 Semantic Scholar 数据库。
    适用于查找研究论文、学术文献、技术报告等。"""
```

返回 JSON 字符串，格式与 `search_web` 一致。

### 配置

无需新增配置字段。两个引擎均不需要 API Key。

### Agent 集成

`src/agents/summarizer.py` 的 `create_summarizer_agent` 中将 `search_papers` 加入 tools 列表，Agent 可同时使用 `search_web` 和 `search_papers`。

### 依赖

- `arxiv`（PyPI 包）
- `httpx`（用于 Semantic Scholar API）

## 影响范围

| 文件 | 变更 |
|------|------|
| `src/tools/search.py` | 新增 `PaperResult`、`_search_arxiv`、`_search_semantic_scholar`、`search_papers` |
| `src/tools/__init__.py` | 导出 `search_papers` |
| `src/agents/summarizer.py` | tools 列表加入 `search_papers` |
| `.env.example` | 添加论文搜索注释 |
| `requirements.txt` / `pyproject.toml` | 添加 `arxiv`、`httpx` 依赖 |
