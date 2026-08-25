"""JSON-safe run-budget calculations used by graph nodes and evaluation artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Config
from src.state import BudgetUsage, RunBudget


def budget_from_config(config: Config) -> RunBudget:
    """Copy mutable environment settings into the immutable persisted run contract."""
    return RunBudget.model_validate(config.budget.model_dump())


def _elapsed_seconds(created_at: str | None) -> float:
    if not created_at:
        return 0.0
    try:
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def aggregate_budget_usage(
    task_results: dict[str, dict[str, Any]],
    *,
    created_at: str | None = None,
) -> BudgetUsage:
    """Aggregate persisted task-level usage; unavailable costs stay unavailable."""
    total_tokens = 0
    costs: list[float] = []
    has_unavailable_cost = False
    for result in task_results.values():
        usage = result.get("token_usage") or {}
        usage_total = usage.get("total_tokens")
        if isinstance(usage_total, int) and usage_total >= 0:
            total_tokens += usage_total
        elif isinstance(usage.get("input_tokens"), int) or isinstance(usage.get("output_tokens"), int):
            total_tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        cost = result.get("estimated_cost")
        if isinstance(cost, (int, float)) and cost >= 0:
            costs.append(float(cost))
        elif usage:
            has_unavailable_cost = True

    return BudgetUsage(
        total_tokens=total_tokens,
        estimated_cost=round(sum(costs), 12) if costs and not has_unavailable_cost else None,
        cost_status="estimated" if costs and not has_unavailable_cost else "unavailable",
        elapsed_seconds=round(_elapsed_seconds(created_at), 3),
    )


def budget_exceeded(budget: RunBudget, usage: BudgetUsage) -> str | None:
    """Return a stable failure code when a configured limit has been reached."""
    if usage.elapsed_seconds >= budget.max_elapsed_seconds:
        return "MAX_ELAPSED_SECONDS"
    if budget.max_total_tokens is not None and usage.total_tokens >= budget.max_total_tokens:
        return "MAX_TOTAL_TOKENS"
    if (
        budget.max_estimated_cost is not None
        and usage.estimated_cost is not None
        and usage.estimated_cost >= budget.max_estimated_cost
    ):
        return "MAX_ESTIMATED_COST"
    return None


def with_budget_status(budget: RunBudget, usage: BudgetUsage) -> BudgetUsage:
    """Return an immutable usage snapshot annotated with its current exhaustion state."""
    reason = budget_exceeded(budget, usage)
    return usage.model_copy(update={"exhausted": reason is not None, "exceeded_reason": reason})
