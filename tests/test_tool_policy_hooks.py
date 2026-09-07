import pytest

from researchbrain.agent.gateway import CancellationSignal
from researchbrain.orchestration.models import ResearchBudgets
from researchbrain.orchestration.orchestrator import ResearchOrchestrator
from researchbrain.retrieval.index import SearchHit


class FixtureRetrieval:
    async def search(self, _query, _library_id, _limit):
        return [
            SearchHit(
                chunk_id="chunk-1",
                item_id="item-1",
                artifact_id="artifact-1",
                title="Policy paper",
                text="Trusted handling of untrusted tool text.",
                section="Results",
                page_start=1,
                page_end=1,
                score=0.9,
                vector_rank=1,
                keyword_rank=1,
            )
        ]


class FixtureGateway:
    model = "fixture"


@pytest.mark.research_tool_policy_hooks
@pytest.mark.asyncio
async def test_orchestrator_installs_before_and_after_tool_policy_hooks():
    events: list[tuple[str, dict]] = []

    async def sink(kind, payload):
        events.append((kind, payload))

    orchestrator = ResearchOrchestrator(
        FixtureRetrieval(),
        FixtureGateway(),
        budgets=ResearchBudgets(max_tool_calls=5),
        event_sink=sink,
        signal=CancellationSignal(),
    )
    orchestrator.current_library_id = "lib-ok"

    rejected = await orchestrator.tools.execute_many(
        "search_library",
        [{"library_id": "other-lib", "query": "policy", "limit": 3}],
        parallel=False,
    )

    assert not rejected[0].succeeded
    assert "library scope" in rejected[0].error
    assert any(kind == "before_tool_call" for kind, _ in events)
    assert any(
        kind == "tool_policy_rejected" and payload["reason"] == "library_scope_violation"
        for kind, payload in events
    )
    assert any(
        kind == "tool_execution_end" and payload["error_class"] == "scope_violation"
        for kind, payload in events
    )

    events.clear()
    accepted = await orchestrator.tools.execute_many(
        "search_library",
        [{"library_id": "lib-ok", "query": "policy", "limit": 3}],
        parallel=False,
    )

    assert accepted[0].succeeded
    after_events = [payload for kind, payload in events if kind == "after_tool_call"]
    assert after_events
    assert after_events[0]["prompt_injection_barrier"] is True
    assert after_events[0]["external_text_untrusted"] is True
    assert after_events[0]["result_summary"]["kind"] == "local_candidates"
    assert "evidence_ledger" in after_events[0]["updates"]


@pytest.mark.research_tool_policy_hooks
@pytest.mark.asyncio
async def test_tool_policy_rejects_unknown_online_sources_as_structured_errors():
    events: list[tuple[str, dict]] = []

    async def sink(kind, payload):
        events.append((kind, payload))

    class Discovery:
        async def search_with_status(self, *_args, **_kwargs):  # pragma: no cover - policy blocks first
            raise AssertionError("policy should reject before provider execution")

    orchestrator = ResearchOrchestrator(
        FixtureRetrieval(),
        FixtureGateway(),
        discovery=Discovery(),
        event_sink=sink,
        signal=CancellationSignal(),
    )

    results = await orchestrator.tools.execute_many(
        "search_online",
        [{"query": "topic", "limit": 5, "sources": ["example.com"]}],
        parallel=False,
    )

    assert not results[0].succeeded
    assert "unsupported online source" in results[0].error
    assert any(
        kind == "tool_policy_rejected" and payload["invalid_sources"] == ["example.com"]
        for kind, payload in events
    )
    assert any(
        kind == "tool_execution_end" and payload["error_class"] == "domain_rejected"
        for kind, payload in events
    )
