"""Snapshot-only search tools used by offline evaluation."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from src.evaluation.dataset import EvaluationDataset


class OfflineSnapshotFixture:
    """Expose one case's approved evidence snapshots through search-tool contracts."""

    fixture_version = "v1"

    def __init__(self, dataset: EvaluationDataset, case: dict[str, Any]):
        self.dataset_id = dataset.dataset_id
        self.case_id = str(case["case_id"])
        self.snapshot_version = str(case.get("snapshot_version") or self.fixture_version)
        self.allowed_source_ids = tuple(case["allowed_source_ids"])
        missing_source_ids = set(self.allowed_source_ids) - set(dataset.snapshots_by_id)
        if missing_source_ids:
            raise ValueError(f"case {self.case_id} references unknown fixture source IDs: {sorted(missing_source_ids)}")
        self._snapshots = [dataset.snapshots_by_id[source_id] for source_id in self.allowed_source_ids]
        self._calls: list[dict[str, Any]] = []
        self.tools: tuple[BaseTool, ...] = (self._build_web_tool(), self._build_paper_tool())

    def _build_web_tool(self) -> BaseTool:
        @tool
        def search_web(query: str, max_results: int = 5) -> str:
            """Retrieve the current evaluation case's approved web evidence snapshots."""
            return self._search("search_web", query, max_results)

        return search_web

    def _build_paper_tool(self) -> BaseTool:
        @tool
        def search_papers(query: str, max_results: int = 5) -> str:
            """Retrieve the current evaluation case's approved paper evidence snapshots."""
            return self._search("search_papers", query, max_results)

        return search_papers

    def _search(self, tool_name: str, query: str, max_results: int) -> str:
        """Return only materialized case evidence; this path never performs I/O or network calls."""
        limit = max(1, int(max_results))
        results = [self._tool_source(snapshot) for snapshot in self._snapshots[:limit]]
        returned_source_ids = [result["source_id"] for result in results]
        self._calls.append(
            {
                "tool": tool_name,
                "query": str(query),
                "max_results": limit,
                "returned_source_ids": returned_source_ids,
            }
        )
        return json.dumps(
            {
                "provider": "offline_snapshot_fixture",
                "dataset_id": self.dataset_id,
                "snapshot_version": self.snapshot_version,
                "case_id": self.case_id,
                "results": results,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _tool_source(snapshot: dict[str, Any]) -> dict[str, Any]:
        """Keep the online search payload shape while preserving captured evidence metadata."""
        return {
            "source_id": snapshot["source_id"],
            "source_type": snapshot.get("source_type") or "web",
            "provider": snapshot.get("provider") or "offline_snapshot",
            "title": snapshot.get("title"),
            "url": snapshot.get("canonical_url"),
            "canonical_url": snapshot.get("canonical_url"),
            "content": snapshot["evidence_excerpt"],
            "evidence_excerpt": snapshot["evidence_excerpt"],
            "content_hash": snapshot["content_hash"],
            "retrieved_at": snapshot.get("retrieved_at"),
            "locator": snapshot.get("locator"),
        }

    def audit(self) -> dict[str, Any]:
        """Return serializable evidence-access metadata for one graph execution."""
        emitted_source_ids = []
        for call in self._calls:
            for source_id in call["returned_source_ids"]:
                if source_id not in emitted_source_ids:
                    emitted_source_ids.append(source_id)
        return {
            "fixture_version": self.fixture_version,
            "dataset_id": self.dataset_id,
            "case_id": self.case_id,
            "snapshot_version": self.snapshot_version,
            "allowed_source_ids": list(self.allowed_source_ids),
            "call_count": len(self._calls),
            "calls": list(self._calls),
            "emitted_source_ids": emitted_source_ids,
            "emitted_source_scope_violation_count": len(set(emitted_source_ids) - set(self.allowed_source_ids)),
        }
