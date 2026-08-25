import json
from datetime import datetime, timedelta, timezone

from src.cache import SQLiteSearchCache, build_cache_key
from src.tools import search


def test_search_cache_key_changes_when_result_contract_changes():
    base = {
        "tool_name": "search_web",
        "query": "  Agent   evaluation ",
        "provider_config": {"primary": "tavily", "configured": True},
        "language": "auto",
        "max_results": 5,
        "tool_version": "p1.3",
    }
    key = build_cache_key(**base)

    assert key == build_cache_key(**{**base, "query": "agent evaluation"})
    assert key != build_cache_key(**{**base, "tool_version": "p1.4"})
    assert key != build_cache_key(**{**base, "language": "zh"})
    assert key != build_cache_key(**{**base, "provider_config": {"primary": "ddgs", "configured": True}})


def test_sqlite_search_cache_expiry_and_cached_wrapper_avoid_repeat_producer_calls(tmp_path, monkeypatch):
    cache = SQLiteSearchCache(tmp_path / "cache.db", ttl_seconds=60)
    monkeypatch.setattr(search, "_live_search_cache", lambda: cache)
    calls = {"count": 0}

    def producer():
        calls["count"] += 1
        return json.dumps(
            {
                "provider": "fake",
                "results": [{"source_id": "src_cached", "title": "Cached", "url": "https://example.com"}],
            }
        )

    first = json.loads(search._cached_tool_output("search_web", "same query", 5, {"provider": "fake"}, producer))
    second = json.loads(search._cached_tool_output("search_web", "same query", 5, {"provider": "fake"}, producer))

    assert calls["count"] == 1
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["results"] == first["results"]

    key = build_cache_key(
        tool_name="search_web",
        query="expired",
        provider_config={"provider": "fake"},
        language="auto",
        max_results=5,
        tool_version="p1.3",
    )
    now = datetime.now(timezone.utc)
    cache.put(key, {"results": []}, now=now - timedelta(seconds=120))
    assert cache.get(key, now=now) is None

