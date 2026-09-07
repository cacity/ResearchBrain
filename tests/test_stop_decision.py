import time

import pytest

from researchbrain.agent.gateway import CancellationSignal
from researchbrain.orchestration.models import CoverageItem, ResearchBudgets, ReviewIssue, ReviewResult
from researchbrain.orchestration.orchestrator import ResearchOrchestrator
from tests.test_orchestrator import FixtureGateway, FixtureRetrieval, hit


def make_orchestrator(**budget_overrides):
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[]]),
        FixtureGateway(),
        budgets=ResearchBudgets(**budget_overrides),
    )
    orchestrator.started_at = time.monotonic()
    return orchestrator


def coverage(status="covered"):
    return [
        CoverageItem(
            subquestion_id="Q1",
            question="What is known?",
            status=status,
            evidence_ids=["E1"] if status != "insufficient_evidence" else [],
        )
    ]


def test_stop_decision_reports_ready_to_finish_when_required_coverage_is_met():
    orchestrator = make_orchestrator()

    decision = orchestrator._compute_stop_decision(coverage=coverage(), has_evidence=True)

    assert decision.stop is True
    assert decision.status == "ready_to_finish"
    assert decision.reason == "required_coverage_satisfied"
    assert "综合" in decision.next_requirement


def test_stop_decision_keeps_running_for_uncovered_required_subquestions():
    orchestrator = make_orchestrator()

    decision = orchestrator._compute_stop_decision(
        coverage=coverage("partial"),
        has_evidence=True,
    )

    assert decision.stop is False
    assert decision.status == "running"
    assert decision.reason == "coverage_incomplete"
    assert "Q1" in decision.next_requirement


def test_stop_decision_limits_after_repeated_no_gain_rounds():
    orchestrator = make_orchestrator()

    decision = orchestrator._compute_stop_decision(
        coverage=coverage("partial"),
        no_gain_rounds=2,
        has_evidence=True,
    )

    assert decision.stop is True
    assert decision.status == "limited"
    assert decision.reason == "no_new_relevant_evidence"


def test_stop_decision_fails_cleanly_after_timeout_without_evidence():
    orchestrator = make_orchestrator(soft_timeout_seconds=30)
    orchestrator.started_at = time.monotonic() - 31

    decision = orchestrator._compute_stop_decision(
        coverage=coverage("insufficient_evidence"),
        has_evidence=False,
    )

    assert decision.stop is True
    assert decision.status == "failed"
    assert decision.reason == "timeout"


def test_stop_decision_waits_for_approval_or_background_work():
    orchestrator = make_orchestrator()

    decision = orchestrator._compute_stop_decision(
        coverage=coverage("partial"),
        has_evidence=True,
        waiting_for="approval",
    )

    assert decision.stop is True
    assert decision.status == "waiting"
    assert decision.reason == "waiting_for_approval"


def test_stop_decision_blocks_finish_when_reviewer_has_issues_without_revision_budget():
    orchestrator = make_orchestrator(max_revision_rounds=0)
    review = ReviewResult(
        blocking=[
            ReviewIssue(
                type="unsupported_claim",
                claim="unsupported",
                reason="No cited evidence entails the claim.",
            )
        ]
    )

    decision = orchestrator._compute_stop_decision(
        coverage=coverage(),
        has_evidence=True,
        review=review,
    )

    assert decision.stop is True
    assert decision.status == "limited"
    assert decision.reason == "review_blocking_issues"
    assert "删除" in decision.next_requirement


def test_stop_decision_reports_cancelled_signal():
    signal = CancellationSignal()
    signal.cancel()
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[]]),
        FixtureGateway(),
        budgets=ResearchBudgets(),
        signal=signal,
    )
    orchestrator.started_at = time.monotonic()

    decision = orchestrator._compute_stop_decision(coverage=[], has_evidence=False)

    assert decision.stop is True
    assert decision.status == "cancelled"
    assert decision.reason == "cancelled"


@pytest.mark.asyncio
async def test_stop_decision_event_is_emitted_in_orchestrator_run():
    events = []

    async def event_sink(event_type, payload):
        events.append((event_type, payload))

    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        FixtureGateway(),
        event_sink=event_sink,
    )

    await orchestrator.run("library-1", "storm response", evidence_limit=5)

    stop_events = [payload for event_type, payload in events if event_type == "stop_decision"]
    assert stop_events
    assert stop_events[0]["turn"] == "gap_assessment"
    assert stop_events[0]["reason"] in {"coverage_incomplete", "required_coverage_satisfied"}
    assert "next_requirement" in stop_events[0]
