import hashlib
import json

from src.evaluation.dataset import load_evaluation_dataset
from src.evaluation.fixtures import OfflineSnapshotFixture


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _dataset_dir(tmp_path):
    dataset_dir = tmp_path / "v1"
    excerpt = "A captured, offline-only evidence excerpt."
    content_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    (dataset_dir / "manifest.json").parent.mkdir(parents=True)
    (dataset_dir / "manifest.json").write_text(
        json.dumps({"dataset_id": "fixture-test-v1", "status": "ready"}), encoding="utf-8"
    )
    _write_jsonl(
        dataset_dir / "cases.jsonl",
        [
            {
                "case_id": "case_1",
                "topic": "offline test topic",
                "snapshot_version": "v1",
                "allowed_source_ids": ["src_allowed"],
                "expected_facets": ["offline"],
                "target_task_count": {"min": 1, "max": 2},
            }
        ],
    )
    _write_jsonl(
        dataset_dir / "source_snapshots.jsonl",
        [
            {
                "source_id": "src_allowed",
                "canonical_url": "https://example.com/evidence",
                "title": "Captured source",
                "provider": "capture",
                "source_type": "web",
                "retrieved_at": "2026-08-24T00:00:00+00:00",
                "content_hash": content_hash,
                "evidence_excerpt": excerpt,
                "locator": "Abstract",
            },
            {
                "source_id": "src_other",
                "canonical_url": "https://example.com/other",
                "title": "Other source",
                "provider": "capture",
                "source_type": "web",
                "retrieved_at": "2026-08-24T00:00:00+00:00",
                "content_hash": hashlib.sha256(b"Other evidence").hexdigest(),
                "evidence_excerpt": "Other evidence",
                "locator": "Abstract",
            },
        ],
    )
    _write_jsonl(
        dataset_dir / "claim_annotations.jsonl",
        [{"case_id": "case_1", "claim_id": "claim_1", "label": "supported", "source_ids": ["src_allowed"]}],
    )
    _write_jsonl(dataset_dir / "materialization_failures.jsonl", [])
    return dataset_dir


def test_fixture_returns_only_case_allowlisted_snapshot_payloads(tmp_path):
    dataset = load_evaluation_dataset(_dataset_dir(tmp_path))
    fixture = OfflineSnapshotFixture(dataset, dataset.cases[0])

    web_payload = json.loads(fixture.tools[0].invoke({"query": "anything", "max_results": 10}))
    paper_payload = json.loads(fixture.tools[1].invoke({"query": "anything", "max_results": 10}))

    assert web_payload["provider"] == "offline_snapshot_fixture"
    assert [item["source_id"] for item in web_payload["results"]] == ["src_allowed"]
    assert [item["source_id"] for item in paper_payload["results"]] == ["src_allowed"]
    assert all(item["canonical_url"] == "https://example.com/evidence" for item in web_payload["results"])
    audit = fixture.audit()
    assert audit["call_count"] == 2
    assert audit["emitted_source_ids"] == ["src_allowed"]
    assert audit["emitted_source_scope_violation_count"] == 0


def test_dataset_loader_rejects_annotation_outside_case_allowlist(tmp_path):
    dataset_dir = _dataset_dir(tmp_path)
    _write_jsonl(
        dataset_dir / "claim_annotations.jsonl",
        [{"case_id": "case_1", "claim_id": "claim_1", "label": "supported", "source_ids": ["src_other"]}],
    )

    try:
        load_evaluation_dataset(dataset_dir)
    except ValueError as error:
        assert "outside its case allowlist" in str(error)
    else:
        raise AssertionError("expected an invalid annotation source to be rejected")
