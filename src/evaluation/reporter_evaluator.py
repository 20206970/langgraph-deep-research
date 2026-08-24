"""Reporter Agent 评估器"""

import re
from dataclasses import dataclass

from src.agents import create_reporter_agent
from src.llm import create_llm


# 报告应包含的章节
REQUIRED_SECTIONS = [
    "背景",
    "核心",
    "风险",
    "挑战",
    "参考来源",
    "结论",
]


@dataclass
class ReporterEvaluationResult:
    """Reporter 评估结果"""
    # 内容存在性
    has_content: bool
    content_length: int

    # 结构完整性
    sections_found: int
    required_sections_missing: list[str]

    # 内容质量
    has_markdown_format: bool
    has_links: bool

    # 综合评分 (0-100)
    score: float


class ReporterEvaluator:
    """评估 Reporter Agent 的输出"""

    def __init__(self):
        self.llm = create_llm()
        self.agent = create_reporter_agent(self.llm)

    def check_sections(self, content: str) -> dict:
        """检查报告章节完整性"""
        content_lower = content.lower()

        found_sections = []
        missing_sections = []

        for section in REQUIRED_SECTIONS:
            # 检查是否包含该章节（支持多种格式）
            if section.lower() in content_lower or f"## {section}" in content:
                found_sections.append(section)
            else:
                missing_sections.append(section)

        return {
            "sections_found": len(found_sections),
            "required_sections_missing": missing_sections,
            "found_sections": found_sections,
        }

    def check_formatting(self, content: str) -> dict:
        """检查格式和链接"""
        # 移除思考过程
        if "<think>" in content:
            content = content.split("</think>")[-1].strip()

        # 检查 Markdown 格式
        has_markdown = bool(re.search(r'^#{1,3}\s+', content, re.MULTILINE))

        # 检查链接
        links = re.findall(r'\[([^\]]+)\]\(([^\)]+)\)', content)
        has_links = len(links) > 0

        return {
            "has_markdown_format": has_markdown,
            "has_links": has_links,
            "link_count": len(links),
        }

    def calculate_score(self, result: dict) -> float:
        """计算综合评分 (0-100)"""
        score = 0.0

        # 内容存在 (15分)
        if result["has_content"]:
            score += 15

        # 内容长度 (15分)
        length = result["content_length"]
        if length > 2000:
            score += 15
        elif length > 1000:
            score += 10
        elif length > 500:
            score += 5

        # 章节完整性 (40分)
        total_sections = len(REQUIRED_SECTIONS)
        found = result["sections_found"]
        if total_sections > 0:
            section_score = (found / total_sections) * 40
            score += section_score

        # 格式和链接 (30分)
        if result["has_markdown_format"]:
            score += 15
        if result["has_links"]:
            score += 15

        return round(score, 1)

    def evaluate(
        self,
        topic: str,
        tasks: list[dict],
        task_results: list[str],
        sources: list,
    ) -> ReporterEvaluationResult:
        """评估 Reporter 的输出"""
        # 构建输入
        tasks_block = []
        for i, (task, result) in enumerate(zip(tasks, task_results), 1):
            tasks_block.append(f"""### 任务 {i}: {task.get('title', '')}
- 任务目标：{task.get('intent', '')}
- 检索查询：{task.get('query', '')}
- 任务总结：{result}
""")

        # 构建来源列表
        sources_block = []
        for s in sources:
            url = s.url if hasattr(s, 'url') else s.get("url") if isinstance(s, dict) else None
            title = s.title if hasattr(s, 'title') else s.get("title") if isinstance(s, dict) else url
            if url:
                sources_block.append(f"- [{title}]({url})")

        prompt = f"""研究主题：{topic}

任务概览：
{''.join(tasks_block)}

参考来源：
{chr(10).join(sources_block) if sources_block else "无来源信息"}

请根据以上任务总结和来源链接生成最终研究报告。

要求：
1. 在报告中适当位置添加来源引用，使用 Markdown 链接格式
2. 参考来源格式：[标题](URL)"""

        response = self.agent.invoke({"messages": [("user", prompt)]})
        output = response.get("messages", [])[-1].content

        # 检查章节
        sections = self.check_sections(output)

        # 检查格式
        formatting = self.check_formatting(output)

        # 内容长度（移除思考过程后）
        content = output
        if "<think>" in content:
            content = content.split("</think>")[-1].strip()

        # 构建结果
        result = {
            "has_content": bool(content.strip()),
            "content_length": len(content),
            "sections_found": sections["sections_found"],
            "required_sections_missing": sections["required_sections_missing"],
            "has_markdown_format": formatting["has_markdown_format"],
            "has_links": formatting["has_links"],
        }

        # 计算评分
        result["score"] = self.calculate_score(result)

        return ReporterEvaluationResult(**result)

    def print_result(self, result: ReporterEvaluationResult, topic: str = ""):
        """打印评估结果"""
        print(f"\n{'='*50}")
        if topic:
            print(f"主题: {topic}")
        print(f"{'='*50}")
        print(f"有内容: {'OK' if result.has_content else 'FAIL'}")
        print(f"内容长度: {result.content_length} 字符")
        print(f"发现章节: {result.sections_found}/{len(REQUIRED_SECTIONS)}")

        if result.required_sections_missing:
            print(f"缺失章节: {', '.join(result.required_sections_missing)}")

        print(f"Markdown 格式: {'OK' if result.has_markdown_format else 'FAIL'}")
        print(f"有链接引用: {'OK' if result.has_links else 'FAIL'}")
        print(f"{'='*50}")
        print(f"综合评分: {result.score}/100")