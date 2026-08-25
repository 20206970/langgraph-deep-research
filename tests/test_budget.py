from src.budget import aggregate_budget_usage, budget_exceeded, with_budget_status
from src.repository import SQLiteRepository
from src.state import RunBudget, TaskItem, TaskPlan


def test_budget_aggregates_tokens_and_marks_explicit_token_limit():
    budget = RunBudget(max_total_tokens=100, max_elapsed_seconds=300)
    usage = aggregate_budget_usage(
        {
            "task_1": {
                "token_usage": {"input_tokens": 60, "output_tokens": 40},
                "estimated_cost": 0.01,
            }
        }
    )

    assert usage.total_tokens == 100
    assert budget_exceeded(budget, usage) == "MAX_TOTAL_TOKENS"
    assert with_budget_status(budget, usage).exhausted is True


def test_budget_keeps_cost_unavailable_when_any_task_lacks_pricing():
    usage = aggregate_budget_usage(
        {
            "priced": {"token_usage": {"total_tokens": 10}, "estimated_cost": 0.01},
            "unpriced": {"token_usage": {"total_tokens": 10}, "estimated_cost": None},
        }
    )

    assert usage.total_tokens == 20
    assert usage.estimated_cost is None
    assert usage.cost_status == "unavailable"


def test_repository_persists_the_budget_snapshot_used_to_create_a_run(tmp_path):
    repository = SQLiteRepository(tmp_path / "budget.db")
    plan = repository.create_plan(
        TaskPlan(topic="budget", tasks=[TaskItem(id=1, title="Task", intent="intent", query="query")])
    )
    repository.confirm_plan(plan["plan"]["plan_id"], plan["plan"]["plan_version"])
    budget = RunBudget(max_tasks=1, max_search_attempts=2, max_total_tokens=200)

    created = repository.create_run(plan["plan"]["plan_id"], plan["plan"]["plan_version"], budget=budget)

    assert created["run"]["budget"] == budget.model_dump(mode="json")
    repository.close()
