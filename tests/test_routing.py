import pytest

from src.config import get_config
from src.llm import create_llm, model_versions


@pytest.fixture(autouse=True)
def clear_config_cache():
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def test_role_models_fall_back_to_openai_model_and_allow_single_role_override(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "baseline-model")
    monkeypatch.setenv("SUMMARIZER_MODEL", "research-model")

    assert model_versions() == {
        "router": "baseline-model",
        "planner": "baseline-model",
        "summarizer": "research-model",
        "reporter": "baseline-model",
        "repair": "baseline-model",
        "judge": "baseline-model",
    }
    summarizer = create_llm("summarizer")
    planner = create_llm("planner")

    assert summarizer.research_role_metadata["model"] == "research-model"
    assert summarizer.research_role_metadata["max_tokens"] == 4096
    assert planner.research_role_metadata["model"] == "baseline-model"
    assert planner.research_role_metadata["max_tokens"] == 1200


def test_default_llm_keeps_legacy_unbounded_output_behavior(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "baseline-model")

    llm = create_llm()

    assert llm.research_role_metadata == {
        "role": "default",
        "model": "baseline-model",
        "temperature": 0.0,
        "max_tokens": None,
    }

