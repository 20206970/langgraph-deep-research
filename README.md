# LangGraph Deep Research Assistant
- Tavily 搜索后端
- FastAPI 服务接口

## 架构

```text
用户输入 → Planner Agent → 并行任务 → Summarizer Agent → Reporter Agent → 报告
                ↓                              ↓
         短期记忆（内存）              长期记忆（ChromaDB）
```

## 快速开始

### 1. 安装依赖

```bash
cd langgraph-deepresearch
pip install -e .
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的 API Key
```

必需配置：

- `OPENAI_API_KEY`
- `TAVILY_API_KEY`

持久化配置：

- `RESEARCH_DB_PATH`：计划版本、运行产物和 LangGraph checkpoint 共用的 SQLite 文件，默认 `./research.db`。

可选 LangSmith Trace：

- 默认关闭：将 `LANGSMITH_TRACING=true` 并配置 `LANGSMITH_API_KEY` 后启用。
- `LANGSMITH_CAPTURE_CONTENT=false` 保持输入输出隐藏；Trace metadata 只包含运行标识、模型/Prompt 版本和脱敏标记。
- LangSmith 未配置或不可达时自动降级，不影响本地事件和研究流程。

P1.3 模型路由、缓存与预算：

- 每个角色默认回退到 `OPENAI_MODEL`；只在需要时设置 `ROUTER_MODEL`、`PLANNER_MODEL`、`SUMMARIZER_MODEL`、`REPORTER_MODEL`、`REPAIR_MODEL` 或 `JUDGE_MODEL`。
- 可选的 `*_TEMPERATURE` 和 `*_MAX_TOKENS` 仅影响对应角色。`MODEL_PRICING_JSON` 未提供时只记录 Token，成本状态为 `unavailable`，不会猜测价格。
- `SEARCH_CACHE_ENABLED=true` 使用 `RESEARCH_DB_PATH` 中的 SQLite TTL 缓存；缓存命中会保留来源快照并在任务和 SSE 事件中标记 `cache_hit=true`。离线评测快照不会进入此缓存。
- 新运行会固化 `RUN_MAX_TASKS=5`、`RUN_MAX_SEARCH_ATTEMPTS=3`、`RUN_MAX_FORMAT_REPAIRS=1` 与 `RUN_MAX_ELAPSED_SECONDS=300`。`RUN_MAX_TOTAL_TOKENS` 和 `RUN_MAX_ESTIMATED_COST` 留空时禁用；已并行启动任务的总量限制是协作式的，报告会明确标注受预算影响的范围。

离线路由对比示例：

```bash
python -m src.evaluation.cli --offline --dataset evaluation_data/v1-draft \
  --runs 3 --route-label baseline --model-label OPENAI_MODEL --output-dir evaluation_results/baseline

# 仅通过环境变量覆盖需要比较的角色模型，再写入独立目录。
SUMMARIZER_MODEL=your-research-model python -m src.evaluation.cli --offline \
  --dataset evaluation_data/v1-draft --runs 3 --route-label candidate \
  --model-label candidate --output-dir evaluation_results/candidate
```

每个产物目录的 `config.json`、`results.json` 与 `summary.md` 分别保存路由指纹、任务级 Token/缓存/成本指标和聚合质量、延迟、失败率指标；非空输出目录会拒绝覆盖。

### 3. 运行服务

```bash
python -m src.main
# 或
uvicorn src.main:app --reload
```

## API 使用

### 同步请求

```bash
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "Python 异步编程最佳实践"}'
```

### 流式请求

```bash
curl -X POST http://localhost:8000/research/stream \
  -H "Content-Type: application/json" \
  -d '{"topic": "Python 异步编程最佳实践"}'
```

响应为标准 SSE，每个事件包含 `event`、`id` 和 JSON `data`，例如：

```text
event: task_completed
id: evt_xxx
data: {"event_id":"evt_xxx","run_id":"run_xxx","task_id":"task_xxx","type":"task_completed","payload":{"status":"succeeded","attempt":1}}
```

### 计划确认后执行

`POST /plans` 只生成并持久化计划，不会调用搜索工具。确认后才能创建运行：

```bash
# 1. 创建计划，响应中的 plan 包含 plan_id 和 plan_version
curl -X POST http://localhost:8000/plans \
  -H "Content-Type: application/json" \
  -d '{"topic": "Python 异步编程最佳实践"}'

# 2. 确认计划
curl -X POST http://localhost:8000/plans/{plan_id}/versions/1/confirm

# 3. 基于已确认版本执行；服务会使用 run_id 作为 checkpoint thread_id
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"plan_id": "{plan_id}", "plan_version": 1}'
```

失败运行可通过 `POST /runs/{run_id}/resume` 恢复；失败任务可通过
`POST /runs/{run_id}/tasks/{task_id}/retry` 重试。重试仅执行目标任务，并追加新的报告版本。

## API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/healthz` | GET | 健康检查 |
| `/research` | POST | 同步执行研究 |
| `/research/stream` | POST | 持久化一键研究并通过标准 SSE 推送事件 |
| `/plans` | POST | 生成并保存未确认计划，不执行搜索 |
| `/plans/{plan_id}/versions/{version}` | GET / PUT | 查询计划或保存新的计划版本 |
| `/plans/{plan_id}/versions/{version}/confirm` | POST | 确认计划版本 |
| `/runs` | POST | 基于已确认计划执行研究 |
| `/runs/{run_id}` | GET | 查询运行、任务尝试和报告版本 |
| `/runs/{run_id}/resume` | POST | 从 SQLite checkpoint 恢复运行 |
| `/runs/{run_id}/tasks/{task_id}/retry` | POST | 重试失败任务并生成新报告版本 |

## 项目来源说明

本项目基于 [datawhalechina/hello-agents](https://github.com/datawhalechina/hello-agents.git) 第十四章的思路进行实现，当前版本使用 LangGraph 对整体流程进行了重新组织与重构。

感谢原项目作者及贡献者的开源分享。

## 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph)
- [LangChain](https://github.com/langchain-ai/langchain)
- [Tavily](https://www.tavily.com/)
- [datawhalechina/hello-agents](https://github.com/datawhalechina/hello-agents.git)

## License

请在使用本项目时同时关注原参考项目的许可证要求，并确保遵循相关开源协议。
