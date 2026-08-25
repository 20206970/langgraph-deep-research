"""评估命令行工具"""

import argparse
import json
import sys
from pathlib import Path

from src.evaluation.runner import EvaluationRunner, OfflineEvaluationRunner


def main():
    parser = argparse.ArgumentParser(description="评估 LangGraph Deep Research Agent")
    parser.add_argument("topic", nargs="?", help="研究主题")
    parser.add_argument("--batch", "-b", help="批量评估文件路径 (每行一个主题)")
    parser.add_argument("--check-urls", "-c", action="store_true", help="验证 URL 有效性 (会增加耗时)")
    parser.add_argument("--planner-only", "-p", action="store_true", help="只评估 Planner")
    parser.add_argument("--output", "-o", default="evaluation_results.json", help="输出 JSON 文件路径")
    parser.add_argument("--dataset", type=Path, help="版本化离线评测数据集目录")
    parser.add_argument("--offline", action="store_true", help="使用数据集快照替代所有在线检索")
    parser.add_argument("--runs", type=int, default=3, help="离线评测每个 case 的重复运行次数，默认 3")
    parser.add_argument("--model-label", default="unspecified", help="记录到离线产物中的模型标签")
    parser.add_argument("--route-label", default="default", help="记录到离线产物中的模型路由标签")
    parser.add_argument("--prompt-version", default="p0.2", help="记录到离线产物中的 Prompt 版本")
    parser.add_argument("--output-dir", type=Path, help="离线评测产物目录；非空目录会拒绝覆盖")

    args = parser.parse_args()

    if args.dataset:
        if not args.offline:
            parser.error("--dataset requires --offline to prevent accidental live search")
        if args.topic or args.batch or args.planner_only or args.check_urls:
            parser.error("--dataset cannot be combined with legacy topic, batch, planner, or URL-check options")
        try:
            result = OfflineEvaluationRunner(
                args.dataset,
                runs=args.runs,
                model_label=args.model_label,
                route_label=args.route_label,
                prompt_version=args.prompt_version,
            ).run(args.output_dir)
        except (ValueError, FileExistsError) as error:
            parser.error(str(error))
        print(f"\n>>> 离线评测产物已保存到: {result['output_dir']}")
        return

    if args.offline:
        parser.error("--offline requires --dataset")

    runner = EvaluationRunner(check_urls=args.check_urls)

    if args.batch:
        # 批量评估
        with open(args.batch, "r", encoding="utf-8") as f:
            topics = [line.strip() for line in f if line.strip()]

        print(f"批量评估 {len(topics)} 个主题...")
        results = runner.evaluate_batch(topics, output_file=args.output)

    elif args.topic:
        # 单个主题评估
        if args.planner_only:
            from src.evaluation.planner_evaluator import PlannerEvaluator
            evaluator = PlannerEvaluator()
            result = evaluator.evaluate(args.topic)
            evaluator.print_result(result, args.topic)
        else:
            result = runner.evaluate_full(args.topic)

        # 保存单个结果
        output_data = {
            "evaluation_time": str(Path(__file__).stat().st_mtime),
            "total_topics": 1,
            "results": [result.to_dict()],
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n>>> 结果已保存到: {args.output}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
