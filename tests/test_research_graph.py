import json

from src.graph import research


class FakeMessage:
    def __init__(self, content: str, message_type: str = "ai", name: str = ""):
        self.content = content
        self.type = message_type
        self.name = name


class FakePlanner:
    def __init__(self, output: str):
        self.output = output

    def invoke(self, _input):
        return {"messages": [FakeMessage(self.output)]}


class FakeSummarizer:
    def invoke(self, payload):
        prompt = payload["messages"][-1][1]
        if "first query" in prompt:
            title = "First source"
            url = "https://example.com/first"
            source_id = "src_first"
        else:
            title = "Second source"
            url = "https://example.com/second"
            source_id = "src_second"
        summary = "根据可访问来源整理了任务目标、关键机制、工程限制和可验证的结论。" * 10
        return {
            "messages": [
                FakeMessage(
                    json.dumps(
                        {
                            "provider": "fake",
                            "results": [
                                {
                                    "source_id": source_id,
                                    "source_type": "web",
                                    "provider": "fake",
                                    "title": title,
                                    "url": url,
                                    "canonical_url": url,
                                    "content_hash": f"hash_{source_id}",
                                    "evidence_excerpt": "A bounded source excerpt.",
                                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    "tool",
                    "search_web",
                ),
                FakeMessage(
                    json.dumps(
                        {
                            "summary": summary,
                            "claims": [
                                {
                                    "text": f"{title} supports the task conclusion.",
                                    "source_ids": [source_id],
                                    "evidence_status": "supported",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        }


class FakeReporter:
    def __init__(self):
        self.prompts = []

    def invoke(self, payload):
        self.prompts.append(payload["messages"][-1][1])
        return {"messages": [FakeMessage("# Final report\n\nStructured report.")]}


class FakeLlm:
    def __init__(self, repair_output: str = "not json"):
        self.repair_output = repair_output
        self.repair_calls = 0

    def invoke(self, _input):
        self.repair_calls += 1
        return FakeMessage(self.repair_output)


def _disable_memory(monkeypatch):
    monkeypatch.setattr(research, "get_long_term_memory", lambda: None)
    monkeypatch.setattr(research, "get_short_term_memory", lambda _llm: None)


def test_graph_aggregates_parallel_results_by_task_id_and_plan_order(monkeypatch):
    planner_output = json.dumps(
        {
            "tasks": [
                {"title": "First", "intent": "first intent", "query": "first query"},
                {"title": "Second", "intent": "second intent", "query": "second query"},
            ]
        }
    )
    reporter = FakeReporter()
    _disable_memory(monkeypatch)
    monkeypatch.setattr(research, "_create_llm", lambda: FakeLlm())
    monkeypatch.setattr(research, "create_planner_agent", lambda _llm: FakePlanner(planner_output))
    monkeypatch.setattr(research, "create_summarizer_agent", lambda _llm: FakeSummarizer())
    monkeypatch.setattr(research, "create_reporter_agent", lambda _llm: reporter)

    result = research.create_research_graph().invoke({"topic": "test topic"})

    assert result["run"]["status"] == "succeeded"
    assert len(result["task_results"]) == 2
    assert len(result["sources"]) == 2
    assert result["report"].startswith("# Final report\n\nStructured report.")
    prompt = reporter.prompts[0]
    assert prompt.index("### 任务 1: First") < prompt.index("### 任务 2: Second")
    assert "First source" in prompt
    assert "Second source" in prompt
    assert "`src_first`" in result["report"]


def test_invalid_planner_output_does_not_dispatch_search(monkeypatch):
    llm = FakeLlm("still invalid")
    dispatched = {"summarizer": 0}
    _disable_memory(monkeypatch)
    monkeypatch.setattr(research, "_create_llm", lambda: llm)
    monkeypatch.setattr(research, "create_planner_agent", lambda _llm: FakePlanner("not json"))

    def unexpected_summarizer(_llm):
        dispatched["summarizer"] += 1
        raise AssertionError("invalid plans must not dispatch search")

    monkeypatch.setattr(research, "create_summarizer_agent", unexpected_summarizer)

    result = research.create_research_graph().invoke({"topic": "test topic"})

    assert result["plan"]["parse_status"] == "rejected"
    assert result["tasks"] == []
    assert result["report"].find("未执行检索") >= 0
    assert dispatched["summarizer"] == 0
    assert llm.repair_calls == 1


def test_reporter_uses_plan_order_when_result_map_is_reverse_order(monkeypatch):
    reporter = FakeReporter()
    _disable_memory(monkeypatch)
    monkeypatch.setattr(research, "_create_llm", lambda: FakeLlm())
    monkeypatch.setattr(research, "create_reporter_agent", lambda _llm: reporter)

    state = {
        "topic": "test topic",
        "run": {"run_id": "run_test", "thread_id": "run_test", "topic": "test topic", "status": "running"},
        "plan": {
            "plan_id": "plan_test",
            "plan_version": 1,
            "topic": "test topic",
            "parse_status": "valid",
            "tasks": [
                {"task_id": "task_first", "id": 1, "title": "First", "intent": "first intent", "query": "first query"},
                {"task_id": "task_second", "id": 2, "title": "Second", "intent": "second intent", "query": "second query"},
            ],
        },
        "task_results": {
            "task_second": {"task_id": "task_second", "status": "succeeded", "summary": "second summary", "source_ids": []},
            "task_first": {"task_id": "task_first", "status": "succeeded", "summary": "first summary", "source_ids": []},
        },
        "sources": {},
    }

    research.reporter_node(state)

    prompt = reporter.prompts[0]
    assert prompt.index("任务结果：first summary") < prompt.index("任务结果：second summary")
