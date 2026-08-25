"""Human-readable reports for offline evaluation artifacts."""

from __future__ import annotations

from typing import Any


def _value(value: Any) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_summary_markdown(config: dict[str, Any], aggregate: dict[str, Any]) -> str:
    """Render a summary whose reported values are all stored in ``results.json``."""
    dataset = config["dataset"]
    elapsed = aggregate["total_elapsed_ms"]
    facet = aggregate["facet_coverage_proxy"]
    citation = aggregate["citation_coverage"]
    estimated_cost = aggregate["estimated_cost"]
    failure_count = dataset["manifest"].get("failure_count", 0)
    return "\n".join(
        [
            "# v1 快照离线评测汇总",
            "",
            f"- 数据集：`{dataset['manifest']['dataset_id']}`（{dataset['manifest'].get('status', 'unknown')}）",
            f"- 路由标签：`{config['route_label']}`；模型标签：`{config['model_label']}`；Prompt 版本：`{config['prompt_version']}`",
            f"- 重复次数：{config['runs']}；离线快照模式：`{config['offline']}`",
            f"- 原始逐 case/run 记录见 `results.json`；本文件全部数值均由其 aggregate 字段生成。",
            "",
            "## 汇总",
            "",
            "| 指标 | 数值 |",
            "| --- | --- |",
            f"| Case-run 数 | {_value(aggregate['case_run_count'])} |",
            f"| Case-run 失败率 | {_value(aggregate['failure_rate'])} |",
            f"| Planner 有效率 | {_value(aggregate['planner_valid_rate'])} |",
            f"| 任务失败率 | {_value(aggregate['task_failure_rate'])} |",
            f"| 总耗时中位数 / 最小 / 最大（ms） | {_value(elapsed['median'])} / {_value(elapsed['min'])} / {_value(elapsed['max'])} |",
            f"| 维度覆盖 proxy 中位数 | {_value(facet['median'])} |",
            f"| 结构化引用覆盖率中位数 | {_value(citation['median'])} |",
            f"| 来源范围违规数 | {_value(aggregate['source_scope_violation_count'])} |",
            f"| 缓存命中任务数 / 命中率 | {_value(aggregate['cache_hit_task_count'])} / {_value(aggregate['cache_hit_rate'])} |",
            f"| 已知估算成本中位数 | {_value(estimated_cost['median'])} |",
            "",
            "## 口径与限制",
            "",
            "- 离线仅指检索证据来自固定快照；模型推理仍可能调用配置的 LLM 服务。",
            "- Fixture 不发起网页、论文库或 URL 可访问性请求；每个 case 只能返回其 `allowed_source_ids`。",
            "- 维度覆盖是规划文本的词面匹配 proxy，不代表语义覆盖；人工标注也尚未与模型结论逐条对齐，因此不输出证据蕴含正确率。",
            f"- 数据集保留 {failure_count} 条物化抓取缺口；这些候选来源不会进入离线检索证据。",
            "",
        ]
    )
