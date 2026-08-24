# 深度研究离线评测集初版草案

此目录是待审阅的评测集设计稿，不是可直接用于离线回归的正式数据集。

## 覆盖范围

`cases.jsonl` 包含 12 个案例，覆盖技术综述、论文对比、工程选型、安全建议、时效信息、中文监管材料、跨语种综合和证据不足负例。每个案例定义待覆盖维度、合理任务数和预期输出边界，而不预置模型答案。

`source_candidates.jsonl` 为每个案例列出 2 个候选一手来源。它们尚未被抓取，不含 `evidence_excerpt`、`content_hash` 或 `source_id`，不会被离线 Runner 当作有效来源快照。

`claim_annotation_draft.jsonl` 列出每个案例 2 条待人工确认的关键结论。`expected_label` 只说明预期标注方向，不是事实支持结论。

## 审阅重点

1. 案例是否覆盖目标系统最重要的检索、规划、引用和失败边界。
2. 题目表述是否会引入不必要的实时性、法律解释或医学建议风险。
3. 候选来源是否应替换为更权威、稳定或中文的一手材料。
4. 每条关键结论是否可被现有候选来源直接支持；不能支持的应删除或改为 `insufficient_expected`。

## 确认后的物化步骤

1. 固定案例、候选来源和关键结论，创建不可覆盖的 `evaluation_data/v1/`。
2. 人工打开每个候选来源，保存不超过 1,500 字的必要证据片段、抓取时间、规范化 URL 与 SHA-256 内容哈希。
3. 按 `canonical_url + content_hash` 生成 `source_id`，写入 `source_snapshots.jsonl`。
4. 将每条结论人工标为 `supported`、`unsupported` 或 `insufficient`，并记录关联 `source_id`。
5. 用物化后的快照运行离线回归；候选 URL 或内容变化时创建新版本，不能覆盖旧版本。
