# LangGraph Deep Research

> A privacy-aware multi-agent deep research assistant built with LangGraph, featuring hybrid PDF RAG, citation tracing, and reproducible offline evaluation.

面向学术调研与用户私有论文研究的多 Agent 应用。系统将研究任务拆分为规划、并行检索与摘要、报告生成，并通过结构化状态、来源引用、离线评测和文档生命周期管理控制生成过程。

## Features

- **可控研究工作流**：使用 LangGraph 编排 `Planner -> Summarizer -> Reporter`，支持计划确认、取消、失败恢复和单任务重试；运行状态与 checkpoint 持久化到 SQLite。
- **私有论文工作区**：用户注册和 JWT 身份认证后，可上传 PDF 或 Markdown；文档、研究记录、检索范围和恢复操作均由服务端按用户隔离。
- **论文解析与索引**：PDF 优先通过 Docling 解析，失败时降级至 MarkItDown；以 Markdown 二级标题为逻辑父级，结合三级标题和段落生成父子块，并保留页码和标题路径。
- **混合检索与精排**：BGE-M3 向量检索与 SQLite FTS5 BM25 并行召回，通过 RRF 融合、物理父块聚合，再使用 `BAAI/bge-reranker-v2-m3` 精排；reranker 不可用时显式保留降级状态。
- **来源可追溯**：研究产物使用稳定的任务、来源与结论 ID，报告引用可关联网页或用户文档的标题、页码和章节定位。
- **可观测与可评估**：本地结构化事件和 SSE 推送任务进度；LangSmith 为可选 Trace，默认隐藏内容；固定离线快照数据集用于路由、Prompt 和流程回归比较。
- **运行边界控制**：支持搜索 TTL 缓存、任务/重试/时长/Token/成本预算；预算耗尽和工具故障均会生成明确状态，而非伪成功结果。

## Architecture

```text
Vue 3 client
    |
    | REST / SSE + JWT
    v
FastAPI ----------------------------------------------------+
    |                                                       |
    +--> LangGraph research flow                            +--> Document worker
    |      Planner -> parallel Summarizer -> Reporter       |      PDF / Markdown ingestion
    |                                                       |      Docling -> MarkItDown fallback
    |                                                       |      chunking -> index
    v                                                       v
SQLite: users, plans, runs, events, FTS5           Chroma: memory and document vectors
    |                                                       |
    +-------------------- hybrid retrieval ----------------+
             vector search + BM25 -> RRF -> parent reranker
```

## Tech Stack

| Area | Components |
| --- | --- |
| Agent and backend | Python, LangGraph, LangChain, FastAPI, Pydantic, SQLite |
| Retrieval | BGE-M3, ChromaDB, SQLite FTS5, BM25, RRF, BGE Reranker |
| Document processing | Docling, MarkItDown, Pillow, optional OpenAI-compatible or Hugging Face VLM |
| Client and observability | Vue 3, Vite, SSE, LangSmith |
| Quality and operations | Pytest, offline fixtures, model routing, TTL cache, budget controls |

## Quick Start

### Prerequisites

- Python 3.10 or later
- Node.js 18 or later for the Vue client
- A compatible LLM API key and Tavily API key
- Optional NVIDIA GPU for local BGE-M3 and reranker inference

### Backend

```bash
git clone https://github.com/<your-account>/langgraph-deep-research.git
cd langgraph-deep-research

python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[dev]"

cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
```

Edit `.env` before running. At minimum, configure the following values:

```dotenv
OPENAI_API_KEY=<your-llm-api-key>
OPENAI_BASE_URL=<openai-compatible-base-url>
OPENAI_MODEL=<model-name>
TAVILY_API_KEY=<your-tavily-key>

# Required for production deployment.
APP_ENV=production
AUTH_JWT_SECRET=<random-secret-with-at-least-32-characters>
```

Start the API service:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

Start the document-ingestion worker in a separate terminal. Uploaded documents remain queued until this worker processes them:

```bash
python -m src.documents.worker --poll-seconds 1
```

The health endpoint is available at `GET /healthz`.

### Frontend

For local development:

```bash
cd frontend
npm ci
npm run dev
```

The frontend defaults to `http://localhost:8000`. To target another API address, configure the build-time variable:

```bash
VITE_API_BASE_URL=https://api.example.com npm run build
```

For production, run `npm run build` and serve `frontend/dist/` through a static web server such as Nginx or Caddy. The FastAPI application does not serve the generated frontend bundle.

## Document RAG Configuration

The default setup uses local Hugging Face models. On a deployment machine with a 12 GB GPU, explicitly select a GPU and begin with conservative batch sizes:

```dotenv
EMBEDDINGS_PROVIDER=huggingface
EMBEDDINGS_MODEL=BAAI/bge-m3
EMBEDDINGS_DEVICE=cuda:0
EMBEDDINGS_BATCH_SIZE=16

RERANKER_PROVIDER=flagembedding
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_DEVICE=cuda:0
RERANKER_BATCH_SIZE=4
```

Keep production data outside the repository and assign durable absolute paths:

```dotenv
RESEARCH_DB_PATH=/data/deep-research/research.db
CHROMA_PERSIST_DIR=/data/deep-research/chroma-memory
DOCUMENT_STORAGE_ROOT=/data/deep-research/documents
DOCUMENT_CHROMA_PERSIST_DIR=/data/deep-research/chroma-documents
```

Embedding indexes from different models are not interchangeable. Rebuild the corresponding Chroma index after changing the embedding model. See [docs/本地Embedding部署.md](docs/本地Embedding部署.md) for GPU and multi-replica considerations.

## Testing and Evaluation

Run the automated regression suite:

```bash
pytest -q
```

Run the offline evaluation dataset without calling external models:

```bash
python -m src.evaluation.cli --offline --dataset evaluation_data/v1 \
  --runs 3 --route-label baseline --model-label OPENAI_MODEL \
  --output-dir evaluation_results/baseline
```

The runner records model routing, prompts, dataset version, fixture snapshots, quality metrics, latency, cache behavior, token usage, and available cost estimates in a new output directory. It refuses to overwrite an existing output directory.

## Validation Status and Boundaries

- The P2.7 controlled acceptance flow passes text-first PDF conversion through the MarkItDown fallback, parent-child chunking, SQLite/FTS5 and Chroma indexing, hybrid retrieval, reranker degradation, document delete/restore/purge, cross-user isolation, and diagnostic redaction.
- The acceptance run deliberately uses deterministic embedding and reranker adapters. It does **not** claim a completed production validation of real BGE reranker inference.
- Docling image extraction and VLM visual enrichment require model downloads and explicit VLM configuration. They are implemented with unit coverage, but the real-model acceptance path must be revalidated in the deployment environment before being presented as a completed capability.
- LangSmith is disabled by default. When enabled, content capture remains disabled; do not upload raw private documents, images, API keys, authorization headers, or local storage paths to traces.

See [docs/2026-08-26-P2.7验收记录.md](docs/2026-08-26-P2.7验收记录.md) for the reproducible acceptance record.

## Repository Hygiene

Do not commit `.env`, user uploads, SQLite databases, Chroma directories, model caches, frontend dependencies, or evaluation outputs. They may contain credentials, private content, local paths, or machine-specific indexes.

## Upstream Attribution

This repository is an extended implementation inspired by Chapter 14 of [datawhalechina/hello-agents](https://github.com/datawhalechina/hello-agents). It reorganizes the original research flow around LangGraph and adds structured research artifacts, evaluation, authentication, private document RAG, and a Vue client.

This project is not affiliated with the upstream maintainers. Before publishing or redistributing it, retain all required notices and comply with the upstream repository's license and attribution terms.

## Acknowledgements

- [LangGraph](https://github.com/langchain-ai/langgraph)
- [LangChain](https://github.com/langchain-ai/langchain)
- [Tavily](https://www.tavily.com/)
- [datawhalechina/hello-agents](https://github.com/datawhalechina/hello-agents)
