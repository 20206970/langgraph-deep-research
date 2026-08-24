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
| `/research/stream` | POST | 流式执行研究 |
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
