from src.citations import (
    build_citation_report,
    calculate_citation_coverage,
    find_cross_task_references,
    validate_reference_structure,
    validate_source_accessibility,
)


def test_reference_structure_accepts_task_local_unique_source_ids():
    claims = [
        {"claim_id": "claim_1", "task_id": "task_1", "text": "fact", "source_ids": ["src_1"], "evidence_status": "supported"}
    ]

    assert validate_reference_structure(claims, "task_1", {"src_1"}) == []


def test_reference_structure_rejects_missing_duplicate_and_foreign_sources():
    claims = [
        {"claim_id": "missing", "task_id": "task_1", "source_ids": [], "evidence_status": "supported"},
        {"claim_id": "duplicate", "task_id": "task_1", "source_ids": ["src_1", "src_1"], "evidence_status": "supported"},
        {"claim_id": "foreign", "task_id": "task_1", "source_ids": ["src_2"], "evidence_status": "supported"},
    ]

    codes = {issue["code"] for issue in validate_reference_structure(claims, "task_1", {"src_1"})}

    assert {"CLAIM_SOURCE_IDS_MISSING", "CLAIM_SOURCE_IDS_DUPLICATE", "CLAIM_SOURCE_NOT_AVAILABLE"} <= codes


def test_cross_task_references_are_reported():
    claims = [{"claim_id": "claim_1", "task_id": "task_1", "source_ids": ["src_2"]}]
    refs = {"task_1": [{"source_id": "src_1"}], "task_2": [{"source_id": "src_2"}]}

    assert find_cross_task_references(claims, refs)[0]["code"] == "CROSS_TASK_REFERENCE"


def test_citation_coverage_and_accessibility_are_explicit():
    claims = [{"source_ids": ["src_1"]}, {"source_ids": []}]
    access = validate_source_accessibility(
        [{"source_id": "src_1", "canonical_url": "https://example.com"}, {"source_id": "src_2"}],
        fetcher=lambda url: url == "https://example.com",
    )

    assert calculate_citation_coverage(claims) == 0.5
    assert access == [
        {"source_id": "src_1", "accessible": True, "status": "checked"},
        {"source_id": "src_2", "accessible": False, "status": "missing_url"},
    ]


def test_citation_report_collects_task_metrics_without_network_access():
    report = build_citation_report(
        {"task_1": {"claims": [{"claim_id": "claim_1", "task_id": "task_1", "source_ids": ["src_1"], "evidence_status": "supported"}]}},
        {"src_1": {"source_id": "src_1", "canonical_url": "https://example.com"}},
        {"task_1": [{"source_id": "src_1"}]},
    )

    assert report["summary"]["citation_coverage"] == 1.0
    assert report["summary"]["issue_count"] == 0
