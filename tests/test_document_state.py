import json

import pytest

from src.state import DocumentScope, ResearchRun, SCHEMA_VERSION


def test_document_scope_round_trip_is_preserved_in_research_run():
    scope = DocumentScope(
        selection_mode="explicit",
        version_ids=["ver_b", "ver_a"],
        resolved_at="2026-08-25T00:00:00+00:00",
    )
    run = ResearchRun(topic="Compare two papers", owner_id="user_123", document_scope=scope)

    restored = ResearchRun.model_validate_json(json.dumps(run.model_dump(mode="json")))

    assert restored.schema_version == SCHEMA_VERSION == 3
    assert restored.owner_id == "user_123"
    assert restored.document_scope.selection_mode == "explicit"
    assert restored.document_scope.version_ids == ["ver_b", "ver_a"]
    assert restored.document_scope.resolved_at == "2026-08-25T00:00:00+00:00"


def test_document_scope_rejects_ambiguous_or_duplicate_versions():
    with pytest.raises(ValueError, match="empty selection"):
        DocumentScope(selection_mode="none", version_ids=["ver_1"])

    with pytest.raises(ValueError, match="requires document versions"):
        DocumentScope(selection_mode="explicit")

    with pytest.raises(ValueError, match="must be unique"):
        DocumentScope(selection_mode="explicit", version_ids=["ver_1", "ver_1"])


def test_research_run_keeps_an_empty_scope_compatible_with_existing_callers():
    run = ResearchRun(topic="Existing anonymous test")

    assert run.owner_id == ""
    assert run.document_scope.selection_mode == "none"
    assert run.document_scope.version_ids == []
