"""Research workflow using LangGraph"""

import json
import re
from typing import Any, Dict, Iterator, Optional

from langgraph.graph import StateGraph, END
from langgraph.constants import START, Send

from src.agents import create_planner_agent, create_summarizer_agent, create_reporter_agent
from src.config import get_config
from src.memory.short_term import get_short_term_memory, get_memory_context
from src.memory.long_term import get_long_term_memory, search_long_term_memory, save_research_memory


def _create_llm():
    """Create LLM instance from config"""
    from langchain_openai import ChatOpenAI
    config = get_config()
    return ChatOpenAI(
        model=config.llm.model,
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        temperature=config.llm.temperature,
    )


def _parse_tasks(response: str) -> list[dict]:
    """Parse Agent output to extract task list"""
    match = re.search(r'\{[\s\S]*"tasks"[\s\S]*\}', response)
    if not match:
        return []

    try:
        data = json.loads(match.group())
        tasks = data.get("tasks", [])
        return [
            {
                "id": i + 1,
                "title": t.get("title", f"任务{i+1}"),
                "intent": t.get("intent", ""),
                "query": t.get("query", ""),
            }
            for i, t in enumerate(tasks)
        ]
    except json.JSONDecodeError:
        return []


def planner_node(state: dict) -> dict:
    """Planner node: generates task list"""
    llm = _create_llm()
    agent = create_planner_agent(llm)

    topic = state.get("topic", "")

    # Get memory context
    try:
        short_mem = get_short_term_memory(llm)
        long_mem = get_long_term_memory()
        context = get_memory_context(short_mem) if short_mem else ""
        long_context = "\n".join(search_long_term_memory(topic, long_mem) if long_mem else [])
    except Exception:
        context = ""
        long_context = ""

    prompt = f"""当前研究主题：{topic}

历史上下文：
{context}

长期记忆参考：
{long_context}

请为此主题规划研究任务。"""

    response = agent.invoke({"messages": [("user", prompt)]})
    output = response.get("messages", [])[-1].content

    tasks = _parse_tasks(output)

    # Fallback task if no tasks generated
    if not tasks:
        tasks = [{
            "id": 1,
            "title": "基础背景梳理",
            "intent": "收集主题的核心背景与最新动态",
            "query": f"{topic} 最新进展",
        }]

    return {
        "tasks": tasks,
        "loop_count": state.get("loop_count", 0) + 1,
    }


def search_summarize_node(state: dict) -> dict:
    """Search and summarize node"""
    llm = _create_llm()
    agent = create_summarizer_agent(llm)

    task = state.get("task", {})
    topic = state.get("topic", "")

    query = task.get("query", "")

    prompt = f"""任务主题：{topic}
任务名称：{task.get("title", "")}
任务目标：{task.get("intent", "")}
检索查询：{query}

请执行搜索并生成任务总结。"""

    response = agent.invoke({"messages": [("user", prompt)]})
    output = response.get("messages", [])[-1].content

    # Extract summary content
    summary = output
    if "<think>" in summary:
        summary = summary.split("</think>")[-1].strip()

    return {
        "task_results": [summary],
        "sources": [query],
    }


def reporter_node(state: dict) -> dict:
    """Reporter node: generate final report"""
    llm = _create_llm()
    agent = create_reporter_agent(llm)

    topic = state.get("topic", "")
    tasks = state.get("tasks", [])
    results = state.get("task_results", [])

    # Build task overview
    tasks_block = []
    for i, (task, result) in enumerate(zip(tasks, results), 1):
        tasks_block.append(f"""### 任务 {i}: {task.get('title', '')}
- 任务目标：{task.get('intent', '')}
- 检索查询：{task.get('query', '')}
- 任务总结：{result}
""")

    prompt = f"""研究主题：{topic}

任务概览：
{''.join(tasks_block)}

请根据以上任务总结生成最终研究报告。"""

    response = agent.invoke({"messages": [("user", prompt)]})
    report = response.get("messages", [])[-1].content

    # Clean output
    if "<think>" in report:
        report = report.split("</think>")[-1].strip()

    # Save to long-term memory
    try:
        long_mem = get_long_term_memory()
        if long_mem:
            save_research_memory(topic, results, report, long_mem)
    except Exception:
        pass

    return {"report": report}


def _split_tasks(state: dict) -> list[Send]:
    """Split tasks to parallel nodes"""
    tasks = state.get("tasks", [])
    return [Send("search_summarize", {**state, "task": task}) for task in tasks]


def create_research_graph():
    """Create research workflow graph"""
    workflow = StateGraph(dict)

    # Add nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("search_summarize", search_summarize_node)
    workflow.add_node("reporter", reporter_node)

    # Set entry point
    workflow.add_edge(START, "planner")

    # Conditional edges: from planner to parallel tasks
    workflow.add_conditional_edges(
        "planner",
        _split_tasks,
        ["search_summarize"]
    )

    # Edges: from tasks to reporter
    workflow.add_edge("search_summarize", "reporter")
    workflow.add_edge("reporter", END)

    return workflow.compile()


# Global graph instance
_research_graph = None


def get_research_graph():
    """Get research workflow graph (cached)"""
    global _research_graph
    if _research_graph is None:
        _research_graph = create_research_graph()
    return _research_graph