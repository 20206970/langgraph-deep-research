"""Planner Agent 评估器"""

import json
import re
from dataclasses import dataclass
from typing import Optional

from src.agents import create_planner_agent
from src.llm import create_llm


@dataclass
class PlannerEvaluationResult:
    """Planner 评估结果"""
    # 格式验证
    is_valid_json: bool
    json_parse_error: Optional[str]

    # 任务质量
    task_count: int
    tasks_have_title: int
    tasks_have_intent: int
    tasks_have_query: int

    # 任务多样性（通过查询关键词判断）
    unique_queries: int

    # 综合评分 (0-100)
    score: float


class PlannerEvaluator:
    """评估 Planner Agent 的输出"""

    def __init__(self):
        self.llm = create_llm()
        self.agent = create_planner_agent(self.llm)

    def parse_output(self, output: str) -> tuple[bool, Optional[dict], Optional[str]]:
        """解析 Planner 输出为 JSON"""
        # 尝试提取 JSON
        match = re.search(r'\{[\s\S]*"tasks"[\s\S]*\}', output)
        if not match:
            return False, None, "未找到 JSON 格式输出"

        try:
            data = json.loads(match.group())
            return True, data, None
        except json.JSONDecodeError as e:
            return False, None, f"JSON 解析错误: {e}"

    def evaluate_task_quality(self, tasks: list) -> dict:
        """评估任务列表质量"""
        if not tasks:
            return {
                "task_count": 0,
                "tasks_have_title": 0,
                "tasks_have_intent": 0,
                "tasks_have_query": 0,
                "unique_queries": 0,
            }

        tasks_have_title = sum(1 for t in tasks if t.get("title"))
        tasks_have_intent = sum(1 for t in tasks if t.get("intent"))
        tasks_have_query = sum(1 for t in tasks if t.get("query"))

        # 计算唯一查询（去重）
        queries = [t.get("query", "").lower().strip() for t in tasks if t.get("query")]
        unique_queries = len(set(queries))

        return {
            "task_count": len(tasks),
            "tasks_have_title": tasks_have_title,
            "tasks_have_intent": tasks_have_intent,
            "tasks_have_query": tasks_have_query,
            "unique_queries": unique_queries,
        }

    def calculate_score(self, result: dict) -> float:
        """计算综合评分 (0-100)"""
        score = 0.0

        # JSON 格式正确性 (30分)
        if result["is_valid_json"]:
            score += 30

        # 任务数量合理性 (20分): 1-8个任务得满分
        task_count = result["task_count"]
        if 1 <= task_count <= 8:
            score += 20
        elif task_count > 8:
            score += 10  # 超过8个适当扣分

        # 字段完整性 (30分)
        completeness = (
            result["tasks_have_title"] / max(task_count, 1) * 10 +
            result["tasks_have_intent"] / max(task_count, 1) * 10 +
            result["tasks_have_query"] / max(task_count, 1) * 10
        )
        score += completeness

        # 任务多样性 (20分): 唯一查询越多越好
        diversity = min(result["unique_queries"] / max(task_count, 1), 1.0) * 20
        score += diversity

        return round(score, 1)

    def evaluate(self, topic: str) -> PlannerEvaluationResult:
        """评估 Planner 对某个主题的输出"""
        prompt = f"""当前研究主题：{topic}

请为此主题规划研究任务。"""

        response = self.agent.invoke({"messages": [("user", prompt)]})
        output = response.get("messages", [])[-1].content

        # 解析输出
        is_valid_json, data, json_error = self.parse_output(output)

        tasks = data.get("tasks", []) if data else []

        # 评估任务质量
        quality = self.evaluate_task_quality(tasks)

        # 构建结果
        result = {
            "is_valid_json": is_valid_json,
            "json_parse_error": json_error,
            **quality,
        }

        # 计算评分
        result["score"] = self.calculate_score(result)

        return PlannerEvaluationResult(**result)

    def evaluate_batch(self, topics: list[str]) -> list[PlannerEvaluationResult]:
        """批量评估多个主题"""
        return [self.evaluate(topic) for topic in topics]

    def print_result(self, result: PlannerEvaluationResult, topic: str = ""):
        """打印评估结果"""
        print(f"\n{'='*50}")
        if topic:
            print(f"主题: {topic}")
        print(f"{'='*50}")
        print(f"JSON 格式正确: {'OK' if result.is_valid_json else 'FAIL'}")
        if result.json_parse_error:
            print(f"解析错误: {result.json_parse_error}")
        print(f"任务数量: {result.task_count}")
        print(f"  - 有标题: {result.tasks_have_title}")
        print(f"  - 有意图: {result.tasks_have_intent}")
        print(f"  - 有查询: {result.tasks_have_query}")
        print(f"唯一查询数: {result.unique_queries}")
        print(f"{'='*50}")
        print(f"综合评分: {result.score}/100")