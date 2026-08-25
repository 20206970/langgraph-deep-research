import json

from src.graph import research
from src.state import DocumentScope, RunStatus, TaskItem


class _Message:
    def __init__(self, content, message_type="ai", name=""):
        self.content = content
        self.type = message_type
        self.name = name


class _DocumentAwareAgent:
    def __init__(self, document_tools):
        self.document_tools = list(document_tools or [])
        self.prompts = []

    def invoke(self, payload):
        self.prompts.append(payload["messages"][-1][1])
        source_id = "src_doc_123"
        tool_messages = []
        if self.document_tools:
            tool_messages.append(
                _Message(
                    json.dumps(
                        {
                            "provider": "private_document_rag",
                            "results": [
                                {
                                    "source_id": source_id,
                                    "source_type": "private_document",
                                    "provider": "private_document_rag",
                                    "title": "Private Paper",
                                    "locator": "第 4 页；章节：Paper > Discussion",
                                    "evidence_excerpt": "Bounded source excerpt.",
                                    "retrieved_at": "2026-08-25T00:00:00+00:00",
                                }
                            ],
                            "retrieval": {
                                "scope_version_count": 1,
                                "parent_count": 1,
                                "vector_candidate_count": 2,
                                "bm25_candidate_count": 1,
                                "vector_status": "applied",
                                "reranker_status": "degraded",
                                "reranker_model": "test-reranker",
                                "latency_ms": 7,
                                "error_code": None,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    "tool",
                    "search_private_documents",
                )
            )
        claims = (
            [{"text": "The private paper supports the conclusion.", "source_ids": [source_id], "evidence_status": "supported"}]
            if tool_messages
            else [{"text": "No private source was selected.", "source_ids": [], "evidence_status": "insufficient"}]
        )
        return {
            "messages": [
                *tool_messages,
                _Message(json.dumps({"summary": "x" * 220, "claims": claims}, ensure_ascii=False)),
            ]
        }


def _state(scope):
    task = TaskItem(id=1, title="Task", intent="Use evidence", query="paper evidence")
    return {
        "topic": "private document topic",
        "task": task.model_dump(mode="json"),
        "owner_id": "owner_a",
        "document_scope": scope.model_dump(mode="json"),
        "run": {
            "run_id": "run_document",
            "topic": "private document topic",
            "status": RunStatus.CONFIRMED.value,
            "owner_id": "owner_a",
            "document_scope": scope.model_dump(mode="json"),
        },
        "task_results": {},
        "sources": {},
        "task_source_refs": {},
    }


def test_graph_injects_private_tool_only_for_nonempty_scope_and_preserves_document_citations(monkeypatch):
    selected_tools = []
    factories = []
    agents = []

    def create_agent(_llm, tools=None, *, document_tools=None):
        selected_tools.append((tools, document_tools))
        agent = _DocumentAwareAgent(document_tools)
        agents.append(agent)
        return agent

    def document_tool_factory(owner_id, scope):
        factories.append((owner_id, scope.model_copy(deep=True)))
        return object()

    monkeypatch.setattr(research, "_create_llm", lambda *_args: object())
    monkeypatch.setattr(research, "create_summarizer_agent", create_agent)

    selected_scope = DocumentScope(selection_mode="explicit", version_ids=["version_selected"])
    state = _state(selected_scope)
    # Task state is copied for parallel sends; the durable run snapshot remains
    # authoritative if a copied root field is stale or accidentally altered.
    state["document_scope"] = DocumentScope().model_dump(mode="json")
    result = research.search_summarize_node(state, document_tool_factory=document_tool_factory)

    assert factories == [("owner_a", selected_scope)]
    assert selected_tools[0][0] is None
    assert len(selected_tools[0][1]) == 1
    assert "私有文档" in agents[0].prompts[0]
    source = next(iter(result["sources"].values()))
    assert source["source_type"] == "private_document"
    assert source["locator"] == "第 4 页；章节：Paper > Discussion"
    retrieval_diagnostics = [
        value
        for key, value in result["output_diagnostics"].items()
        if key.endswith(":document_retrieval:1")
    ]
    assert retrieval_diagnostics == [
        {
            "scope_version_count": 1,
            "parent_count": 1,
            "vector_candidate_count": 2,
            "bm25_candidate_count": 1,
            "vector_status": "applied",
            "reranker_status": "degraded",
            "reranker_model": "test-reranker",
            "latency_ms": 7,
            "error_code": None,
        }
    ]

    empty_result = research.search_summarize_node(_state(DocumentScope()), document_tool_factory=document_tool_factory)
    assert len(factories) == 1
    assert selected_tools[1][1] is None
    assert empty_result["sources"] == {}
    assert "未选择私有文档" in agents[1].prompts[0]


def test_reporter_formats_private_document_title_page_and_heading_without_a_public_url():
    sources = {
        "src_doc_123": {
            "source_id": "src_doc_123",
            "source_type": "private_document",
            "title": "Private Paper",
            "locator": "第 4 页；章节：Paper > Discussion",
        }
    }
    results = {
        "task_1": {
            "task_id": "task_1",
            "status": "succeeded",
            "summary": "summary",
            "source_ids": ["src_doc_123"],
            "claims": [
                {
                    "text": "supported claim",
                    "source_ids": ["src_doc_123"],
                    "evidence_status": "supported",
                }
            ],
        }
    }
    tasks = [{"task_id": "task_1", "title": "Task", "intent": "intent", "query": "query"}]

    prompt = research._build_report_prompt("topic", tasks, results, sources)
    index = research._append_source_index("# Report", results, sources)

    assert "Private Paper（第 4 页；章节：Paper > Discussion）" in prompt
    assert "Private Paper（第 4 页；章节：Paper > Discussion）" in index
    assert "](None)" not in prompt
