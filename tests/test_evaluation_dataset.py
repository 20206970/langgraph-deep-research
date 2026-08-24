import hashlib
import json

from src.evaluation.dataset import materialize_draft, read_jsonl


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_materialize_draft_builds_versioned_snapshots_and_annotations(tmp_path):
    draft_dir = tmp_path / "v1-draft"
    output_dir = tmp_path / "v1"
    excerpt = "An authoritative evidence excerpt."
    _write_jsonl(
        draft_dir / "cases.jsonl",
        [{"case_id": "case_1", "topic": "topic", "snapshot_version": "draft"}],
    )
    _write_jsonl(
        draft_dir / "source_candidates.jsonl",
        [{"source_candidate_id": "cand_1", "case_id": "case_1", "title": "Source", "source_type": "web"}],
    )
    _write_jsonl(
        draft_dir / "claim_annotation_draft.jsonl",
        [{"claim_candidate_id": "claim_1", "case_id": "case_1", "text": "A claim"}],
    )
    (draft_dir / "manifest.json").write_text(
        json.dumps({"source_policy": {"priority": ["official"]}}), encoding="utf-8"
    )
    _write_jsonl(
        draft_dir / "materialization" / "batch-a-source-snapshots.jsonl",
        [
            {
                "source_candidate_id": "cand_1",
                "canonical_url": "https://example.com/page?utm_source=test",
                "title": "Source",
                "provider": "manual_capture",
                "source_type": "web",
                "retrieved_at": "2026-08-24T00:00:00+00:00",
                "evidence_excerpt": excerpt,
                "content_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            }
        ],
    )
    _write_jsonl(
        draft_dir / "materialization" / "batch-a-claim-annotations.jsonl",
        [
            {
                "claim_candidate_id": "claim_1",
                "label": "supported",
                "source_candidate_ids": ["cand_1"],
                "annotation_note": "Direct support.",
            }
        ],
    )

    manifest = materialize_draft(draft_dir, output_dir)
    snapshots = read_jsonl(output_dir / "source_snapshots.jsonl")
    annotations = read_jsonl(output_dir / "claim_annotations.jsonl")
    cases = read_jsonl(output_dir / "cases.jsonl")

    assert manifest["status"] == "ready_for_offline_regression"
    assert snapshots[0]["canonical_url"] == "https://example.com/page"
    assert snapshots[0]["source_id"].startswith("src_")
    assert annotations[0]["source_ids"] == [snapshots[0]["source_id"]]
    assert cases[0]["allowed_source_ids"] == [snapshots[0]["source_id"]]
    assert "Deep Research Evaluation Dataset v1" in (output_dir / "README.md").read_text(encoding="utf-8")


def test_materialize_draft_keeps_hash_mismatch_out_of_snapshots(tmp_path):
    draft_dir = tmp_path / "v1-draft"
    output_dir = tmp_path / "v1"
    _write_jsonl(draft_dir / "cases.jsonl", [{"case_id": "case_1", "topic": "topic"}])
    _write_jsonl(
        draft_dir / "source_candidates.jsonl",
        [{"source_candidate_id": "cand_1", "case_id": "case_1", "title": "Source", "source_type": "web"}],
    )
    _write_jsonl(draft_dir / "claim_annotation_draft.jsonl", [])
    (draft_dir / "manifest.json").write_text(json.dumps({"source_policy": {}}), encoding="utf-8")
    _write_jsonl(
        draft_dir / "materialization" / "batch-a-source-snapshots.jsonl",
        [
            {
                "source_candidate_id": "cand_1",
                "canonical_url": "https://example.com/page",
                "evidence_excerpt": "Evidence",
                "content_hash": "incorrect",
            }
        ],
    )
    _write_jsonl(draft_dir / "materialization" / "batch-a-claim-annotations.jsonl", [])

    manifest = materialize_draft(draft_dir, output_dir)
    failures = read_jsonl(output_dir / "materialization_failures.jsonl")

    assert manifest["status"] == "partial_capture"
    assert failures[0]["reason"] == "declared content hash does not match evidence excerpt"
