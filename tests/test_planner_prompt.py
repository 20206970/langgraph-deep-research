"""Planner prompts must advertise the same task ceiling the run budget enforces."""

import json

from src.agents.planner import DEFAULT_MAX_TASKS, planner_system_prompt
from src.graph import research


class FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.type = "ai"


class FakePlanner:
    def __init__(self, output: str):
        self.output = output

    def invoke(self, _input):
        return {"messages": [FakeMessage(self.output)]}


def test_prompt_renders_budget_ceiling():
    prompt = planner_system_prompt(5)

    assert "3-5 个之间" in prompt
    assert "不得超过 5 个" in prompt
    assert "1--5 个" in prompt


def test_prompt_ceiling_is_clamped_to_at_least_one():
    prompt = planner_system_prompt(0)

    assert "1-1 个之间" in prompt
    assert "不得超过 1 个" in prompt


def test_default_prompt_keeps_upper_bound_of_seven():
    prompt = planner_system_prompt()

    assert f"不得超过 {DEFAULT_MAX_TASKS} 个" in prompt


def test_planner_node_passes_budget_max_tasks(monkeypatch):
    from src.config import get_config

    captured = {}

    def fake_agent(_llm, **kwargs):
        captured.update(kwargs)
        return FakePlanner(json.dumps({"tasks": [{"title": "T", "intent": "i", "query": "q"}]}))

    monkeypatch.setenv("RUN_MAX_TASKS", "6")
    # get_config 带 lru_cache：套件中更早的测试可能已缓存旧 env，须清缓存才能读到本次 setenv
    get_config.cache_clear()
    monkeypatch.setattr(research, "_create_llm", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(research, "create_planner_agent", fake_agent)
    monkeypatch.setattr(research, "get_long_term_memory", lambda: None)
    monkeypatch.setattr(research, "get_short_term_memory", lambda _llm: None)

    result = research.planner_node({"topic": "test topic"})

    assert captured["max_tasks"] == 6
    assert result["plan"]["parse_status"] != "rejected"
