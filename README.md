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

## API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/healthz` | GET | 健康检查 |
| `/research` | POST | 同步执行研究 |
| `/research/stream` | POST | 流式执行研究 |

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