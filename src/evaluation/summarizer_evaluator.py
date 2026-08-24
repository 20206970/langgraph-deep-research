"""Summarizer Agent 评估器"""

import json
import re
import httpx
from dataclasses import dataclass
from typing import Optional

from src.agents import create_summarizer_agent
from src.llm import create_llm


@dataclass
class SummarizerEvaluationResult:
    """Summarizer 评估结果"""
    # 内容质量
    has_content: bool
    content_length: int

    # 来源验证
    sources_count: int
    valid_sources_count: int
    source_validity_rate: float

    # 内容深度
    has_multiple_points: bool  # 是否有多个要点
    has_structured_output: bool  # 是否有结构化输出

    # 综合评分 (0-100)
    score: float


class SummarizerEvaluator:
    """评估 Summarizer Agent 的输出"""

    def __init__(self, check_urls: bool = False):
        """
        初始化评估器

        Args:
            check_urls: 是否验证 URL 有效性（会显著增加耗时）
        """
        self.llm = create_llm()
        self.agent = create_summarizer_agent(self.llm)
        self.check_urls = check_urls

    def extract_sources(self, output: str) -> list[dict]:
        """从输出中提取来源列表"""
        sources = []

        # 尝试匹配 JSON 格式的来源
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', output)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                sources = data.get("sources", [])
            except json.JSONDecodeError:
                pass

        return sources

    def check_url_validity(self, url: str, timeout: float = 5.0) -> bool:
        """检查 URL 是否可访问"""
        if not url:
            return False
        try:
            response = httpx.head(url, timeout=timeout, follow_redirects=True)
            return response.status_code < 400
        except Exception:
            return False

    def validate_sources(self, sources: list[dict]) -> tuple[int, int]:
        """
        验证来源有效性

        Returns:
            (有效来源数, 总来源数)
        """
        if not sources:
            return 0, 0

        if not self.check_urls:
            # 不验证时，只检查格式
            valid = sum(1 for s in sources if isinstance(s, dict) and s.get("url"))
            return valid, len(sources)

        # 验证 URL
        valid_count = 0
        for source in sources:
            if isinstance(source, dict) and source.get("url"):
                if self.check_url_validity(source["url"]):
                    valid_count += 1

        return valid_count, len(sources)

    def analyze_content_depth(self, output: str) -> dict:
        """分析内容深度"""
        # 移除思考过程
        if "</think>" in output:
            content = output.split("</think>")[-1].strip()
        else:
            content = output

        # 检查是否有多个要点（列表项数量）
        list_items = re.findall(r'^\s*[-*]|\d+\.', content, re.MULTILINE)
        has_multiple_points = len(list_items) >= 3

        # 检查是否有结构化输出（标题）
        has_headers = bool(re.search(r'^#{1,3}\s+', content, re.MULTILINE))

        return {
            "has_multiple_points": has_multiple_points,
            "has_structured_output": has_headers,
            "content_length": len(content),
        }

    def calculate_score(self, result: dict) -> float:
        """计算综合评分 (0-100)"""
        score = 0.0

        # 内容存在 (20分)
        if result["has_content"]:
            score += 20

        # 内容深度 (30分)
        if result["has_multiple_points"]:
            score += 15
        if result["has_structured_output"]:
            score += 15

        # 来源验证 (30分)
        if result["sources_count"] > 0:
            score += result["source_validity_rate"] * 30

        # 内容长度合理性 (20分)
        length = result["content_length"]
        if length > 200:
            score += 20
        elif length > 100:
            score += 10
        elif length > 50:
            score += 5

        return round(score, 1)

    def evaluate(self, topic: str, task: dict) -> SummarizerEvaluationResult:
        """评估 Summarizer 对某个任务的输出"""
        query = task.get("query", "")

        prompt = f"""任务主题：{topic}
任务名称：{task.get("title", "")}
任务目标：{task.get("intent", "")}
检索查询：{query}

请执行搜索并生成任务总结。

重要：在总结的最后，请以以下 JSON 格式列出你参考的来源：
```json
{{
  "sources": [
    {{"title": "来源标题", "url": "https://..."}},
    ...
  ]
}}
```"""

        response = self.agent.invoke({"messages": [("user", prompt)]})
        output = response.get("messages", [])[-1].content

        # 提取来源
        sources = self.extract_sources(output)
        valid_count, total_count = self.validate_sources(sources)
        validity_rate = valid_count / total_count if total_count > 0 else 0.0

        # 分析内容深度
        depth = self.analyze_content_depth(output)

        # 构建结果
        result = {
            "has_content": bool(output.strip()),
            "content_length": depth["content_length"],
            "sources_count": total_count,
            "valid_sources_count": valid_count,
            "source_validity_rate": validity_rate,
            "has_multiple_points": depth["has_multiple_points"],
            "has_structured_output": depth["has_structured_output"],
        }

        # 计算评分
        result["score"] = self.calculate_score(result)

        return SummarizerEvaluationResult(**result)

    def evaluate_batch(
        self, topic: str, tasks: list[dict]
    ) -> list[SummarizerEvaluationResult]:
        """批量评估多个任务"""
        return [self.evaluate(topic, task) for task in tasks]

    def print_result(self, result: SummarizerEvaluationResult, task_title: str = ""):
        """打印评估结果"""
        print(f"\n{'='*50}")
        if task_title:
            print(f"任务: {task_title}")
        print(f"{'='*50}")
        print(f"有内容: {'OK' if result.has_content else 'FAIL'}")
        print(f"内容长度: {result.content_length} 字符")
        print(f"来源数量: {result.sources_count}")
        print(f"有效来源: {result.valid_sources_count}")
        print(f"来源有效率: {result.source_validity_rate:.1%}")
        print(f"多要点: {'OK' if result.has_multiple_points else 'FAIL'}")
        print(f"结构化: {'OK' if result.has_structured_output else 'FAIL'}")
        print(f"{'='*50}")
        print(f"综合评分: {result.score}/100")