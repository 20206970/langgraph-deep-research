"""Versioned evaluation dataset loading and draft materialization."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.search import canonicalize_url


@dataclass(frozen=True)
class EvaluationDataset:
    """A validated, immutable-on-disk offline evaluation dataset."""

    root: Path
    manifest: dict[str, Any]
    cases: tuple[dict[str, Any], ...]
    snapshots_by_id: dict[str, dict[str, Any]]
    annotations_by_case: dict[str, tuple[dict[str, Any], ...]]
    materialization_failures: tuple[dict[str, Any], ...]

    @property
    def dataset_id(self) -> str:
        return str(self.manifest["dataset_id"])


def _require_non_empty_string(record: dict[str, Any], field: str, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: {field} must be a non-empty string")
    return value


def load_evaluation_dataset(dataset_dir: Path) -> EvaluationDataset:
    """Load v1-style snapshot files and reject broken source relationships early."""
    root = Path(dataset_dir)
    required_files = (
        "manifest.json",
        "cases.jsonl",
        "source_snapshots.jsonl",
        "claim_annotations.jsonl",
        "materialization_failures.jsonl",
    )
    missing = [name for name in required_files if not (root / name).is_file()]
    if missing:
        raise ValueError(f"evaluation dataset is missing required files: {', '.join(missing)}")

    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{root / 'manifest.json'} is not valid JSON") from error
    if not isinstance(manifest, dict):
        raise ValueError("dataset manifest must be a JSON object")
    _require_non_empty_string(manifest, "dataset_id", "dataset manifest")

    cases = read_jsonl(root / "cases.jsonl")
    snapshots = read_jsonl(root / "source_snapshots.jsonl")
    annotations = read_jsonl(root / "claim_annotations.jsonl")
    failures = read_jsonl(root / "materialization_failures.jsonl")

    cases_by_id: dict[str, dict[str, Any]] = {}
    for index, case in enumerate(cases, start=1):
        context = f"cases.jsonl:{index}"
        case_id = _require_non_empty_string(case, "case_id", context)
        _require_non_empty_string(case, "topic", context)
        allowed_source_ids = case.get("allowed_source_ids")
        if not isinstance(allowed_source_ids, list) or not all(isinstance(item, str) and item for item in allowed_source_ids):
            raise ValueError(f"{context}: allowed_source_ids must be a string list")
        if len(allowed_source_ids) != len(set(allowed_source_ids)):
            raise ValueError(f"{context}: allowed_source_ids must be unique")
        if case_id in cases_by_id:
            raise ValueError(f"duplicate case_id: {case_id}")
        cases_by_id[case_id] = case

    snapshots_by_id: dict[str, dict[str, Any]] = {}
    for index, snapshot in enumerate(snapshots, start=1):
        context = f"source_snapshots.jsonl:{index}"
        source_id = _require_non_empty_string(snapshot, "source_id", context)
        excerpt = _require_non_empty_string(snapshot, "evidence_excerpt", context)
        content_hash = _require_non_empty_string(snapshot, "content_hash", context)
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != content_hash:
            raise ValueError(f"{context}: content_hash does not match evidence_excerpt")
        if source_id in snapshots_by_id:
            raise ValueError(f"duplicate source_id: {source_id}")
        snapshots_by_id[source_id] = snapshot

    for case_id, case in cases_by_id.items():
        unknown_source_ids = set(case["allowed_source_ids"]) - set(snapshots_by_id)
        if unknown_source_ids:
            raise ValueError(f"case {case_id} references unknown source IDs: {sorted(unknown_source_ids)}")

    annotations_by_case: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, annotation in enumerate(annotations, start=1):
        context = f"claim_annotations.jsonl:{index}"
        case_id = _require_non_empty_string(annotation, "case_id", context)
        _require_non_empty_string(annotation, "claim_id", context)
        label = _require_non_empty_string(annotation, "label", context)
        source_ids = annotation.get("source_ids")
        if case_id not in cases_by_id:
            raise ValueError(f"{context}: case_id does not exist in cases.jsonl")
        if not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids):
            raise ValueError(f"{context}: source_ids must be a string list")
        unknown_source_ids = set(source_ids) - set(snapshots_by_id)
        if unknown_source_ids:
            raise ValueError(f"{context}: references unknown source IDs: {sorted(unknown_source_ids)}")
        case_allowed_source_ids = set(cases_by_id[case_id]["allowed_source_ids"])
        if not set(source_ids) <= case_allowed_source_ids:
            raise ValueError(f"{context}: references source IDs outside its case allowlist")
        annotations_by_case[case_id].append(annotation)

    return EvaluationDataset(
        root=root.resolve(),
        manifest=manifest,
        cases=tuple(cases),
        snapshots_by_id=snapshots_by_id,
        annotations_by_case={case_id: tuple(items) for case_id, items in annotations_by_case.items()},
        materialization_failures=tuple(failures),
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read non-empty JSONL rows with contextual errors."""
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number} is not valid JSON") from error
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        records.append(record)
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write one normalized JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records)
    path.write_text(f"{content}\n" if content else "", encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_id(canonical_url: str | None, content_hash: str) -> str:
    identity = f"{canonical_url or ''}|{content_hash}"
    return f"src_{_sha256(identity)[:20]}"


def _dataset_readme(manifest: dict[str, int | str]) -> str:
    """Describe an immutable materialized dataset without relying on live URLs."""
    return f"""# Deep Research Evaluation Dataset v1

This directory is a materialized, snapshot-grounded evaluation dataset. Offline
evaluation must use `source_snapshots.jsonl` rather than fetching live URLs.

## Contents

- `cases.jsonl`: evaluation prompts, expected facets, and each case's allowed source IDs.
- `source_snapshots.jsonl`: deduplicated evidence excerpts with canonical URLs, retrieval
  timestamps, and SHA-256 content hashes.
- `claim_annotations.jsonl`: manually reviewed claim labels. A `supported` claim lists
  sources that directly support it; `insufficient` means the reviewed evidence does not
  directly support the claim and must not be presented as a verified fact.
- `materialization_failures.jsonl`: candidates that were visited but could not yield a
  usable evidence snapshot. They are intentionally excluded from offline evidence.

## Dataset State

- Dataset ID: `{manifest['dataset_id']}`
- Status: `{manifest['status']}`
- Cases: {manifest['case_count']}
- Source snapshots: {manifest['source_snapshot_count']}
- Claim annotations: {manifest['claim_annotation_count']}
- Capture gaps: {manifest['failure_count']}

The draft and batch capture records are retained in `evaluation_data/v1-draft/`.
Re-materialize a new dataset version when any source, evidence excerpt, or annotation
changes; do not overwrite this version for live-web changes.
"""


def _snapshot_from_capture(
    capture: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate one capture record and return either a snapshot or a failure row."""
    candidate_id = candidate["source_candidate_id"]
    excerpt = str(capture.get("evidence_excerpt") or "").strip()
    canonical_url = canonicalize_url(capture.get("canonical_url") or capture.get("url"))
    if not excerpt or not canonical_url:
        return None, {
            "source_candidate_id": candidate_id,
            "case_id": candidate["case_id"],
            "reason": capture.get("failure_reason") or "missing canonical URL or evidence excerpt",
        }
    if len(excerpt) > 1_500:
        return None, {
            "source_candidate_id": candidate_id,
            "case_id": candidate["case_id"],
            "reason": "evidence excerpt exceeds 1500 characters",
        }

    content_hash = _sha256(excerpt)
    declared_hash = capture.get("content_hash")
    if declared_hash and declared_hash != content_hash:
        return None, {
            "source_candidate_id": candidate_id,
            "case_id": candidate["case_id"],
            "reason": "declared content hash does not match evidence excerpt",
        }
    return {
        "source_id": _source_id(canonical_url, content_hash),
        "source_candidate_id": candidate_id,
        "case_id": candidate["case_id"],
        "canonical_url": canonical_url,
        "title": str(capture.get("title") or candidate["title"]).strip(),
        "provider": str(capture.get("provider") or "manual_capture").strip(),
        "source_type": str(capture.get("source_type") or candidate["source_type"]).strip(),
        "retrieved_at": capture.get("retrieved_at") or datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash,
        "evidence_excerpt": excerpt,
        "locator": capture.get("locator"),
    }, None


def materialize_draft(draft_dir: Path, output_dir: Path) -> dict[str, int | str]:
    """Create a usable dataset only from verified batch capture and annotation rows."""
    cases = read_jsonl(draft_dir / "cases.jsonl")
    candidates = read_jsonl(draft_dir / "source_candidates.jsonl")
    claim_drafts = read_jsonl(draft_dir / "claim_annotation_draft.jsonl")
    candidate_by_id = {record["source_candidate_id"]: record for record in candidates}
    claim_by_id = {record["claim_candidate_id"]: record for record in claim_drafts}
    capture_files = sorted((draft_dir / "materialization").glob("batch-*-source-snapshots.jsonl"))
    annotation_files = sorted((draft_dir / "materialization").glob("batch-*-claim-annotations.jsonl"))
    if not capture_files or not annotation_files:
        raise ValueError("batch capture and annotation files are required before materialization")

    snapshots: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for path in capture_files:
        for capture in read_jsonl(path):
            candidate_id = capture.get("source_candidate_id")
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None:
                failures.append({"source_candidate_id": candidate_id, "reason": "unknown source candidate"})
                continue
            snapshot, failure = _snapshot_from_capture(capture, candidate)
            if snapshot is not None:
                snapshots.append(snapshot)
            elif failure is not None:
                failures.append(failure)

    snapshots_by_candidate = {snapshot["source_candidate_id"]: snapshot for snapshot in snapshots}
    unique_snapshots: dict[str, dict[str, Any]] = {}
    for candidate_id, snapshot in snapshots_by_candidate.items():
        source_id = snapshot["source_id"]
        existing = unique_snapshots.get(source_id)
        if existing is None:
            unique_snapshots[source_id] = {
                key: value
                for key, value in snapshot.items()
                if key not in {"source_candidate_id", "case_id"}
            }
            unique_snapshots[source_id]["source_candidate_ids"] = [candidate_id]
        else:
            existing["source_candidate_ids"].append(candidate_id)
    annotations: list[dict[str, Any]] = []
    for path in annotation_files:
        for annotation in read_jsonl(path):
            claim_id = annotation.get("claim_candidate_id")
            draft = claim_by_id.get(claim_id)
            if draft is None:
                failures.append({"claim_candidate_id": claim_id, "reason": "unknown claim candidate"})
                continue
            label = str(annotation.get("label") or annotation.get("annotation_status") or "").lower()
            if label not in {"supported", "unsupported", "insufficient"}:
                failures.append({"claim_candidate_id": claim_id, "reason": "invalid claim label"})
                continue
            source_candidate_ids = annotation.get("source_candidate_ids") or []
            source_ids = [
                snapshots_by_candidate[candidate_id]["source_id"]
                for candidate_id in source_candidate_ids
                if candidate_id in snapshots_by_candidate
            ]
            if label == "supported" and not source_ids:
                failures.append({"claim_candidate_id": claim_id, "reason": "supported claim has no captured source"})
                continue
            annotations.append(
                {
                    "claim_id": claim_id,
                    "case_id": draft["case_id"],
                    "text": draft["text"],
                    "label": label,
                    "source_ids": source_ids,
                    "annotation_note": str(annotation.get("annotation_note") or "").strip(),
                }
            )

    source_ids_by_case: dict[str, list[str]] = {}
    for candidate_id, snapshot in snapshots_by_candidate.items():
        case_id = candidate_by_id[candidate_id]["case_id"]
        source_ids_by_case.setdefault(case_id, []).append(snapshot["source_id"])
    materialized_cases = [
        {**case, "allowed_source_ids": sorted(source_ids_by_case.get(case["case_id"], [])), "snapshot_version": "v1"}
        for case in cases
    ]
    annotated_claim_ids = {annotation["claim_id"] for annotation in annotations}
    ready = all(source_ids_by_case.get(case["case_id"]) for case in cases) and len(annotated_claim_ids) == len(claim_drafts)
    if ready and failures:
        dataset_status = "ready_for_offline_regression_with_capture_gaps"
    elif ready:
        dataset_status = "ready_for_offline_regression"
    else:
        dataset_status = "partial_capture"
    manifest = {
        "schema_version": "1",
        "dataset_id": "deep-research-v1",
        "status": dataset_status,
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(materialized_cases),
        "source_snapshot_count": len(unique_snapshots),
        "claim_annotation_count": len(annotations),
        "failure_count": len(failures),
        "source_policy": json.loads((draft_dir / "manifest.json").read_text(encoding="utf-8"))["source_policy"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_jsonl(output_dir / "cases.jsonl", materialized_cases)
    write_jsonl(output_dir / "source_snapshots.jsonl", list(unique_snapshots.values()))
    write_jsonl(output_dir / "claim_annotations.jsonl", annotations)
    write_jsonl(output_dir / "materialization_failures.jsonl", failures)
    (output_dir / "README.md").write_text(_dataset_readme(manifest), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize a reviewed deep-research evaluation dataset")
    parser.add_argument("--draft-dir", type=Path, default=Path("evaluation_data/v1-draft"))
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation_data/v1"))
    args = parser.parse_args()
    result = materialize_draft(args.draft_dir, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
