# LangGraph Deep Research Assistant

基于 LangGraph 的深度研究助手，使用多智能体架构完成网络搜索、研究总结和报告生成。

## 特性

- 多智能体工作流（Planner → Search/Summarize → Reporter）
- 短期记忆：ConversationSummaryBufferMemory
- 长期记忆：ChromaDB 向量数据库
- 支持同步和流式输出
- Tavily 搜索后端

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
- `OPENAI_API_KEY` - OpenAI API Key
- `TAVILY_API_KEY` - Tavily API Key

### 3. 运行服务

```bash
# 开发模式
python -m src.main

# 或使用 uvicorn
uvicorn src.main:app --reload
```

### 4. API 使用

```bash
# 同步请求
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "Python 异步编程最佳实践"}'

# 流式请求
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

## 架构

```
用户输入 → Planner Agent → 并行任务 → Summarizer Agent → Reporter Agent → 报告
                ↓                              ↓
         短期记忆 (内存)              长期记忆 (ChromaDB)
```