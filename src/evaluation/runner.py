"""评估运行器 - 整合所有评估器"""

import json
import re
import subprocess
import time
from contextlib import ExitStack
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, List
from unittest.mock import patch

from src.evaluation.planner_evaluator import PlannerEvaluator, PlannerEvaluationResult
from src.evaluation.summarizer_evaluator import SummarizerEvaluator, SummarizerEvaluationResult
from src.evaluation.reporter_evaluator import ReporterEvaluator, ReporterEvaluationResult
from src.graph.research import get_research_graph
from src.graph import research
from src.agents.summarizer import create_summarizer_agent
from src.evaluation.dataset import EvaluationDataset, load_evaluation_dataset
from src.evaluation.fixtures import OfflineSnapshotFixture
from src.evaluation.metrics import aggregate_case_runs, compute_case_metrics
from src.evaluation.report import render_summary_markdown
from src.budget import budget_from_config
from src.config import get_config
from src.llm import model_versions


@dataclass
class FullEvaluationResult:
    """完整评估结果"""
    topic: str
    planner_result: Optional[PlannerEvaluationResult]
    summarizer_results: list[SummarizerEvaluationResult]
    reporter_result: Optional[ReporterEvaluationResult]

    @property
    def overall_score(self) -> float:
        """计算整体评分"""
        scores = []

        if self.planner_result:
            scores.append(self.planner_result.score * 0.2)  # Planner 权重 20%

        if self.summarizer_results:
            avg_summarizer = sum(r.score for r in self.summarizer_results) / len(self.summarizer_results)
            scores.append(avg_summarizer * 0.4)  # Summarizer 权重 40%

        if self.reporter_result:
            scores.append(self.reporter_result.score * 0.4)  # Reporter 权重 40%

        return round(sum(scores), 1) if scores else 0.0

    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        def safe_asdict(obj):
            if hasattr(obj, '__dataclass_fields__'):
                return {k: safe_asdict(v) for k, v in asdict(obj).items()}
            elif isinstance(obj, list):
                return [safe_asdict(i) for i in obj]
            elif isinstance(obj, dict):
                return {k: safe_asdict(v) for k, v in obj.items()}
            else:
                return obj

        return {
            "topic": self.topic,
            "overall_score": self.overall_score,
            "planner_result": safe_asdict(self.planner_result) if self.planner_result else None,
            "summarizer_results": safe_asdict(self.summarizer_results) if self.summarizer_results else [],
            "reporter_result": safe_asdict(self.reporter_result) if self.reporter_result else None,
        }


class EvaluationRunner:
    """评估运行器"""

    def __init__(self, check_urls: bool = False):
        """
        初始化评估器

        Args:
            check_urls: 是否验证 URL 有效性
        """
        self.planner_evaluator = PlannerEvaluator()
        self.summarizer_evaluator = SummarizerEvaluator(check_urls=check_urls)
        self.reporter_evaluator = ReporterEvaluator()

    def evaluate_planner(self, topic: str) -> PlannerEvaluationResult:
        """评估 Planner"""
        print(f"\n>>> 评估 Planner: {topic}")
        result = self.planner_evaluator.evaluate(topic)
        self.planner_evaluator.print_result(result, topic)
        return result

    def evaluate_summarizer(self, topic: str, tasks: list[dict]) -> list[SummarizerEvaluationResult]:
        """评估 Summarizer"""
        print(f"\n>>> 评估 Summarizer ({len(tasks)} 个任务)")
        results = self.summarizer_evaluator.evaluate_batch(topic, tasks)

        for task, result in zip(tasks, results):
            self.summarizer_evaluator.print_result(result, task.get("title", ""))

        return results

    def evaluate_reporter(
        self,
        topic: str,
        tasks: list[dict],
        task_results: list[str],
        sources: list,
    ) -> ReporterEvaluationResult:
        """评估 Reporter"""
        print(f"\n>>> 评估 Reporter")
        result = self.reporter_evaluator.evaluate(topic, tasks, task_results, sources)
        self.reporter_evaluator.print_result(result, topic)
        return result

    @staticmethod
    def _legacy_task_results(tasks: list[dict], task_results: object) -> list[str]:
        """Adapt the P0.1 keyed artifacts for the existing Reporter evaluator."""
        if isinstance(task_results, list):
            return [str(item) for item in task_results]
        if not isinstance(task_results, dict):
            return []
        return [
            str(task_results.get(str(task.get("task_id") or ""), {}).get("summary") or "")
            for task in tasks
        ]

    @staticmethod
    def _legacy_sources(sources: object) -> list[dict]:
        """Adapt source artifacts to the evaluator's historical ``url`` field."""
        if isinstance(sources, list):
            return sources
        if not isinstance(sources, dict):
            return []
        return [
            {
                "title": source.get("title"),
                "url": source.get("canonical_url") or source.get("url"),
            }
            for source in sources.values()
            if isinstance(source, dict)
        ]

    def evaluate_full(self, topic: str) -> FullEvaluationResult:
        """
        完整评估：运行整个研究流程并评估每个环节

        Args:
            topic: 研究主题

        Returns:
            完整评估结果
        """
        print(f"\n{'#'*60}")
        print(f"# 完整评估: {topic}")
        print(f"{'#'*60}")

        # 1. 评估 Planner
        planner_result = self.evaluate_planner(topic)
        tasks = []
        if planner_result.is_valid_json:
            # 从结果中提取任务（需要重新构建任务列表）
            # 这里简化为使用原始评估结果的任务
            pass

        # 2. 运行完整研究流程
        print(f"\n>>> 运行研究流程...")
        graph = get_research_graph()
        result = graph.invoke({"topic": topic})

        tasks = result.get("tasks", [])
        task_results = self._legacy_task_results(tasks, result.get("task_results", {}))
        sources = self._legacy_sources(result.get("sources", {}))

        # 3. 评估 Summarizer
        summarizer_results = []
        if tasks and task_results:
            # 评估每个任务的结果
            for task in tasks:
                print(f"\n>>> 评估 Summarizer 任务: {task.get('title', '')}")
                result_single = self.summarizer_evaluator.evaluate(topic, task)
                summarizer_results.append(result_single)
                self.summarizer_evaluator.print_result(result_single, task.get("title", ""))

        # 4. 评估 Reporter
        reporter_result = None
        if task_results and sources:
            reporter_result = self.evaluate_reporter(topic, tasks, task_results, sources)

        # 5. 汇总结果
        full_result = FullEvaluationResult(
            topic=topic,
            planner_result=planner_result,
            summarizer_results=summarizer_results,
            reporter_result=reporter_result,
        )

        # 6. 打印汇总
        self._print_summary(full_result)

        return full_result

    def _print_summary(self, result: FullEvaluationResult):
        """打印评估汇总"""
        print(f"\n{'='*60}")
        print(f"评估汇总: {result.topic}")
        print(f"{'='*60}")

        if result.planner_result:
            print(f"Planner 评分: {result.planner_result.score}/100")

        if result.summarizer_results:
            avg = sum(r.score for r in result.summarizer_results) / len(result.summarizer_results)
            print(f"Summarizer 平均评分: {avg:.1f}/100")

        if result.reporter_result:
            print(f"Reporter 评分: {result.reporter_result.score}/100")

        print(f"{'='*60}")
        print(f"整体评分: {result.overall_score}/100")
        print(f"{'='*60}")

    def evaluate_batch(self, topics: list[str], output_file: str = None) -> list[FullEvaluationResult]:
        """
        批量评估多个主题

        Args:
            topics: 主题列表
            output_file: 输出 JSON 文件路径，默认保存到 evaluation_results.json
        """
        results = []
        for topic in topics:
            try:
                result = self.evaluate_full(topic)
                results.append(result)

                # 每次评估完成后保存中间结果
                if output_file:
                    self._save_results(results, output_file)
            except Exception as e:
                print(f"\n评估失败 [{topic}]: {e}")

        # 打印批量汇总
        self.print_batch_summary(results)

        return results

    def _save_results(self, results: List[FullEvaluationResult], output_file: str):
        """保存结果到 JSON 文件"""
        output_path = Path(output_file)

        # 构建输出数据
        output_data = {
            "evaluation_time": datetime.now().isoformat(),
            "total_topics": len(results),
            "results": [r.to_dict() for r in results],
            "summary": self._build_summary(results),
        }

        # 保存到文件
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        print(f"\n>>> 结果已保存到: {output_path}")

    def _build_summary(self, results: List[FullEvaluationResult]) -> dict:
        """构建汇总统计"""
        if not results:
            return {}

        overall_scores = [r.overall_score for r in results]
        planner_scores = [r.planner_result.score for r in results if r.planner_result]
        summarizer_scores = [
            sum(s.score for s in r.summarizer_results) / len(r.summarizer_results)
            for r in results if r.summarizer_results
        ]
        reporter_scores = [r.reporter_result.score for r in results if r.reporter_result]

        return {
            "overall": {
                "average": round(sum(overall_scores) / len(overall_scores), 1),
                "max": max(overall_scores),
                "min": min(overall_scores),
            },
            "planner": {
                "average": round(sum(planner_scores) / len(planner_scores), 1) if planner_scores else 0,
            },
            "summarizer": {
                "average": round(sum(summarizer_scores) / len(summarizer_scores), 1) if summarizer_scores else 0,
            },
            "reporter": {
                "average": round(sum(reporter_scores) / len(reporter_scores), 1) if reporter_scores else 0,
            },
        }

    def print_batch_summary(self, results: list[FullEvaluationResult]):
        """打印批量评估汇总"""
        print(f"\n{'#'*60}")
        print(f"# 批量评估汇总 ({len(results)} 个主题)")
        print(f"{'#'*60}")

        for result in results:
            print(f"\n主题: {result.topic}")
            print(f"  整体评分: {result.overall_score}/100")
            if result.planner_result:
                print(f"  - Planner: {result.planner_result.score}")
            if result.summarizer_results:
                avg = sum(r.score for r in result.summarizer_results) / len(result.summarizer_results)
                print(f"  - Summarizer: {avg:.1f}")
            if result.reporter_result:
                print(f"  - Reporter: {result.reporter_result.score}")

        # 总体统计
        if results:
            overall_scores = [r.overall_score for r in results]
            print(f"\n统计:")
            print(f"  平均分: {sum(overall_scores)/len(overall_scores):.1f}")
            print(f"  最高分: {max(overall_scores)}")
            print(f"  最低分: {min(overall_scores)}")


class OfflineEvaluationRunner:
    """Run the production graph against immutable evidence snapshots only."""

    RUNNER_VERSION = "p1.3-v1"
    _ARTIFACT_KEYS = (
        "run",
        "plan",
        "tasks",
        "task_results",
        "sources",
        "task_source_refs",
        "report",
        "report_artifact",
        "output_diagnostics",
    )

    def __init__(
        self,
        dataset: EvaluationDataset | Path,
        *,
        runs: int = 3,
        model_label: str = "unspecified",
        route_label: str = "default",
        prompt_version: str = "p0.2",
        graph_factory: Callable[[], Any] | None = None,
    ):
        if runs < 1:
            raise ValueError("runs must be at least 1")
        self.dataset = load_evaluation_dataset(dataset) if isinstance(dataset, Path) else dataset
        self.runs = runs
        self.model_label = model_label.strip() or "unspecified"
        self.route_label = route_label.strip() or "default"
        self.prompt_version = prompt_version.strip() or "unspecified"
        self.graph_factory = graph_factory or research.create_research_graph

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return OfflineEvaluationRunner._json_safe(value.model_dump(mode="json"))
        if isinstance(value, dict):
            return {str(key): OfflineEvaluationRunner._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [OfflineEvaluationRunner._json_safe(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")
        return slug[:48] or "unspecified"

    @staticmethod
    def _git_revision() -> str | None:
        project_root = Path(__file__).resolve().parents[2]
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    def _default_output_dir(self) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = "_".join(
            (
                timestamp,
                self._slug(self.dataset.dataset_id),
                self._slug(self.route_label),
                self._slug(self.model_label),
                self._slug(self.prompt_version),
            )
        )
        return Path("evaluation_results") / name

    @staticmethod
    def _prepare_output_dir(output_dir: Path) -> Path:
        output_dir = output_dir.resolve()
        if output_dir.exists() and any(output_dir.iterdir()):
            raise FileExistsError(f"offline evaluation output directory is not empty: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _config(self, started_at: str, output_dir: Path) -> dict[str, Any]:
        config = get_config()
        return {
            "schema_version": "1",
            "started_at": started_at,
            "offline": True,
            "runs": self.runs,
            "model_label": self.model_label,
            "route_label": self.route_label,
            "prompt_version": self.prompt_version,
            "output_dir": str(output_dir),
            "dataset": {
                "path": str(self.dataset.root),
                "manifest": self.dataset.manifest,
            },
            "runner": {
                "name": "OfflineEvaluationRunner",
                "version": self.RUNNER_VERSION,
                "source_fixture": OfflineSnapshotFixture.fixture_version,
                "source_policy": "case-scoped immutable snapshots only",
            },
            "routing": {
                "label": self.route_label,
                "model_versions": model_versions(),
            },
            "budget": budget_from_config(config).model_dump(mode="json"),
            "search_cache": {
                "enabled": config.search_cache.enabled,
                "ttl_seconds": config.search_cache.ttl_seconds,
                "language": config.search_cache.language,
                "tool_version": config.search_cache.tool_version,
                "offline_fixture_bypasses_live_cache": True,
            },
            "git_revision": self._git_revision(),
        }

    def _invoke_snapshot_graph(self, fixture: OfflineSnapshotFixture, topic: str) -> dict[str, Any]:
        """Patch only graph-local dependencies for one run, then restore them unconditionally."""
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    research,
                    "create_summarizer_agent",
                    lambda llm: create_summarizer_agent(llm, tools=fixture.tools),
                )
            )
            stack.enter_context(patch.object(research, "get_long_term_memory", lambda: None))
            stack.enter_context(patch.object(research, "get_short_term_memory", lambda _llm: None))
            stack.enter_context(patch.object(research, "search_long_term_memory", lambda _topic, _memory: []))
            stack.enter_context(patch.object(research, "save_research_memory", lambda *_args, **_kwargs: None))
            graph = self.graph_factory()
            result = graph.invoke({"topic": topic})
        if not isinstance(result, dict):
            raise TypeError("research graph must return a dictionary state")
        return self._json_safe(result)

    def _run_case(self, case: dict[str, Any], run_index: int) -> dict[str, Any]:
        fixture = OfflineSnapshotFixture(self.dataset, case)
        started = time.perf_counter()
        runner_error = None
        try:
            graph_result = self._invoke_snapshot_graph(fixture, str(case["topic"]))
        except Exception as error:
            graph_result = {
                "run": {"status": "failed"},
                "plan": {},
                "task_results": {},
                "sources": {},
                "task_source_refs": {},
                "report": "",
            }
            runner_error = research._safe_error_message(error)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        fixture_audit = fixture.audit()
        metrics = compute_case_metrics(
            case,
            graph_result,
            fixture_audit,
            list(self.dataset.annotations_by_case.get(str(case["case_id"]), ())),
            elapsed_ms,
        )
        graph_run_status = str(graph_result.get("run", {}).get("status") or "failed")
        run_artifact = graph_result.get("run", {})
        configured_budget = budget_from_config(get_config()).model_dump(mode="json")
        source_scope_clean = metrics["sources"]["source_scope_violation_count"] == 0
        status = "succeeded" if graph_run_status == "succeeded" and source_scope_clean and not runner_error else "failed"
        artifacts = {key: graph_result.get(key) for key in self._ARTIFACT_KEYS if key in graph_result}
        return {
            "case_id": case["case_id"],
            "topic": case["topic"],
            "run_index": run_index,
            "status": status,
            "graph_run_status": graph_run_status,
            "routing": {
                "label": self.route_label,
                "model_versions": run_artifact.get("model_versions") or model_versions(),
            },
            "budget": {
                "configured": run_artifact.get("budget") or configured_budget,
                "usage": run_artifact.get("budget_usage") or {},
            },
            "runner_error": runner_error,
            "fixture": fixture_audit,
            "artifacts": artifacts,
            "metrics": metrics,
        }

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run(self, output_dir: Path | None = None) -> dict[str, Any]:
        """Execute every case repeatedly and write immutable, traceable evaluation artifacts."""
        output_dir = self._prepare_output_dir(output_dir or self._default_output_dir())
        started_at = datetime.now(timezone.utc).isoformat()
        config = self._config(started_at, output_dir)
        self._write_json(output_dir / "config.json", config)

        case_runs = []
        for run_index in range(1, self.runs + 1):
            for case in self.dataset.cases:
                case_runs.append(self._run_case(case, run_index))

        results = {
            "schema_version": "1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "case_runs": case_runs,
            "aggregate": aggregate_case_runs(case_runs),
        }
        self._write_json(output_dir / "results.json", results)
        (output_dir / "summary.md").write_text(
            render_summary_markdown(config, results["aggregate"]), encoding="utf-8"
        )
        return {"output_dir": str(output_dir), "config": config, "results": results}
