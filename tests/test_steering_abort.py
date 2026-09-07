import pytest

from researchbrain.agent.gateway import CancellationSignal
from researchbrain.orchestration.evidence import EvidenceLedger
from researchbrain.orchestration.models import CoverageItem, ResearchIntent, ResearchPlan, ResearchSubquestion
from researchbrain.orchestration.orchestrator import ResearchOrchestrator
from researchbrain.retrieval.index import SearchHit


class FixtureRetrieval:
    pass


class FixtureGateway:
    model = "fixture"


@pytest.mark.research_steering_abort
@pytest.mark.asyncio
async def test_steering_distinguishes_constraint_correction_and_follow_up():
    messages = [
        {"kind": "constraint", "content": "排除 multibeam sonar"},
        {"kind": "correction", "content": "不是多波束声呐"},
        {"kind": "follow_up", "content": "下一轮再比较数据集"},
    ]
    events: list[tuple[str, dict]] = []

    async def sink(kind, payload):
        events.append((kind, payload))

    async def steering_source():
        return messages

    orchestrator = ResearchOrchestrator(
        FixtureRetrieval(),
        FixtureGateway(),
        event_sink=sink,
        signal=CancellationSignal(),
        steering_source=steering_source,
    )

    await orchestrator._consume_steering()

    assert [value["kind"] for value in orchestrator.steering_constraints] == ["constraint"]
    assert [value["kind"] for value in orchestrator.steering_corrections] == ["correction"]
    assert [value["kind"] for value in orchestrator.follow_up_queue] == ["follow_up"]
    applied = next(payload for kind, payload in events if kind == "steering_applied")
    assert applied["counts"] == {"constraint": 1, "correction": 1, "clarification": 0, "follow_up": 1}
    assert applied["follow_up_deferred"] is True
    assert any(kind == "follow_up_queued" for kind, _ in events)


@pytest.mark.research_steering_abort
@pytest.mark.asyncio
async def test_new_constraint_revalidates_queries_candidates_and_coverage():
    events: list[tuple[str, dict]] = []

    async def sink(kind, payload):
        events.append((kind, payload))

    orchestrator = ResearchOrchestrator(
        FixtureRetrieval(),
        FixtureGateway(),
        event_sink=sink,
        signal=CancellationSignal(),
    )
    orchestrator.steering_constraints.append({"kind": "constraint", "content": "排除 multibeam sonar"})
    orchestrator._steering_version = 1
    plan = ResearchPlan(
        intent="Study spherical harmonics",
        research_intent=ResearchIntent(
            normalized_question="Study spherical harmonics",
            methods=["球谐分析"],
        ),
        subquestions=[ResearchSubquestion(id="Q1", question="What methods are used?")],
        queries=["spherical harmonics"],
        topic_terms=["spherical harmonics"],
        excluded_terms=[],
    )
    ledger = EvidenceLedger("E")
    ledger.add_local(
        "spherical harmonics",
        [
            SearchHit(
                chunk_id="bad",
                item_id="item-1",
                artifact_id="artifact-1",
                title="Multibeam sonar processing",
                text="This is about multibeam sonar and should be invalidated by steering.",
                section="Methods",
                page_start=1,
                page_end=1,
                score=0.9,
                vector_rank=1,
                keyword_rank=1,
            ),
            SearchHit(
                chunk_id="good",
                item_id="item-2",
                artifact_id="artifact-2",
                title="Spherical harmonic analysis",
                text="This is about spherical harmonic analysis.",
                section="Methods",
                page_start=2,
                page_end=2,
                score=0.8,
                vector_rank=2,
                keyword_rank=2,
            ),
        ],
    )
    coverage = [
        CoverageItem(
            subquestion_id="Q1",
            question="What methods are used?",
            status="covered",
            evidence_ids=["E1"],
        )
    ]

    updated_plan, updated_coverage = await orchestrator._apply_steering_to_research_state(
        plan, ledger, coverage
    )

    assert "multibeam sonar" in updated_plan.excluded_terms
    assert updated_coverage[0].status == "insufficient_evidence"
    assert not updated_coverage[0].evidence_ids
    assert ledger.evidence(include_excluded=False)[0].title == "Spherical harmonic analysis"
    event = next(payload for kind, payload in events if kind == "steering_revalidated")
    assert event["intent_revalidated"] is True
    assert event["query_revalidated"] is True
    assert event["candidate_revalidated"] is True
    assert event["coverage_revalidated"] is True
    assert event["invalidated_evidence_ids"] == ["E1"]
