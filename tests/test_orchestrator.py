import json
import time

import pytest

from researchbrain.agent.deepseek import GenerationError
from researchbrain.orchestration.evidence import EvidenceLedger
from researchbrain.orchestration.models import ResearchBudgets
from researchbrain.orchestration.orchestrator import ResearchOrchestrator, _fallback_plan
from researchbrain.orchestration.state_machine import (
    InvalidResearchTransition,
    ResearchStateMachine,
)
from researchbrain.retrieval.index import SearchHit


def hit(
    chunk: str,
    item: str = "item-1",
    score: float = 0.9,
    page: int | None = 3,
    title: str | None = None,
    text: str | None = None,
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk,
        item_id=item,
        artifact_id="artifact-1",
        title=title or f"Paper {item}",
        text=text or f"Evidence from {chunk} reports a storm response.",
        section="Results",
        page_start=page,
        page_end=page,
        score=score,
        vector_rank=1,
        keyword_rank=1,
    )


class FixtureRetrieval:
    def __init__(self, results: list[list[SearchHit]]):
        self.results = results
        self.queries: list[str] = []

    async def search(self, query, _library_id, _limit):
        self.queries.append(query)
        index = min(len(self.queries) - 1, len(self.results) - 1)
        return self.results[index]


class FixtureGateway:
    model = "fixture-model"

    def __init__(self, *, invalid_citation: bool = False, require_second_round: bool = False):
        self.invalid_citation = invalid_citation
        self.require_second_round = require_second_round
        self.roles: list[str] = []
        self.assessments = 0

    async def generate_structured(self, role, _system, user, schema, signal):
        signal.raise_if_cancelled()
        self.roles.append(role)
        if role == "planner":
            payload = {
                "intent": "Compare the reported storm response",
                "subquestions": [
                    {
                        "id": "Q1",
                        "question": "What response was reported?",
                        "required_level": "fulltext_page",
                    }
                ],
                "queries": ["storm response observations"],
                "topic_terms": ["storm response"],
                "excluded_terms": [],
                "completion_criteria": ["Q1 is supported by full text"],
            }
        elif role == "assessor":
            self.assessments += 1
            partial = self.require_second_round and self.assessments == 1
            evidence = json.loads(user)["evidence"]
            payload = {
                "coverage": [
                    {
                        "subquestion_id": "Q1",
                        "question": "What response was reported?",
                        "status": "partial" if partial else "covered",
                        "required_level": "fulltext_page",
                        "evidence_ids": [evidence[0]["id"]] if evidence else [],
                        "missing": ["independent observation"] if partial else [],
                        "next_queries": ["independent storm observation"] if partial else [],
                    }
                ],
                "next_action": "local_search" if partial else "synthesize",
                "additional_queries": ["independent storm observation"] if partial else [],
                "rationale": "A second source is needed" if partial else "Evidence is sufficient",
            }
        elif role == "relevance":
            request = json.loads(user)
            payload = {
                "judgments": [
                    {
                        "evidence_id": evidence["id"],
                        "relevance": "relevant",
                        "subquestion_ids": ["Q1"],
                        "reason": "The evidence directly addresses the planned response question.",
                    }
                    for evidence in request["evidence"]
                ]
            }
        elif role == "synthesizer":
            citation = "E99" if self.invalid_citation else "E1"
            payload = {
                "answer": f"The full text reports the response [{citation}].",
                "citation_ids": [citation],
                "limitations": [],
            }
        elif role == "scout":
            request = json.loads(user)
            payload = {
                "subquestion_id": request["subquestion"]["id"],
                "evidence_ids": [request["evidence"][0]["id"]],
                "findings": ["The evidence reports a storm response."],
                "missing": [],
                "next_queries": [],
            }
        elif role == "reviewer":
            payload = {
                "blocking": [],
                "warnings": [],
                "missing_subquestions": [],
                "valid_citation_ids": ["E1"],
            }
        else:
            raise AssertionError(f"unexpected role: {role}")
        return schema.model_validate(payload)


class CrossTopicGateway(FixtureGateway):
    async def generate_structured(self, role, _system, user, schema, signal):
        signal.raise_if_cancelled()
        self.roles.append(role)
        if role == "planner":
            payload = {
                "intent": "Review spherical harmonic analysis methods",
                "subquestions": [
                    {
                        "id": "Q1",
                        "question": "Which spherical harmonic analysis methods are used?",
                        "required_level": "fulltext_section",
                    }
                ],
                "queries": ["spherical harmonic analysis methods"],
                "topic_terms": ["球谐", "spherical harmonic"],
                "excluded_terms": ["多波束", "multibeam sonar"],
                "completion_criteria": ["Use same-topic evidence only"],
            }
        elif role == "relevance":
            request = json.loads(user)
            payload = {
                "judgments": [
                    {
                        "evidence_id": evidence["id"],
                        "relevance": "relevant",
                        "subquestion_ids": ["Q1"],
                        "reason": "The permissive model classified every retrieved result as relevant.",
                    }
                    for evidence in request["evidence"]
                ]
            }
        elif role == "assessor":
            evidence = json.loads(user)["evidence"]
            payload = {
                "coverage": [
                    {
                        "subquestion_id": "Q1",
                        "question": "Which spherical harmonic analysis methods are used?",
                        "status": "covered",
                        "required_level": "fulltext_section",
                        "evidence_ids": [evidence[0]["id"]],
                    }
                ],
                "next_action": "synthesize",
                "rationale": "A directly relevant source is available.",
            }
        elif role == "synthesizer":
            payload = {
                "answer": "The relevant paper compares spherical harmonic methods [E2].",
                "citation_ids": ["E2"],
                "limitations": [],
            }
        elif role == "reviewer":
            payload = {
                "blocking": [],
                "warnings": [],
                "missing_subquestions": [],
                "valid_citation_ids": ["E2"],
            }
        else:
            raise AssertionError(f"unexpected role: {role}")
        return schema.model_validate(payload)


class IrrelevantOnlyGateway(FixtureGateway):
    async def generate_structured(self, role, _system, user, schema, signal):
        if role != "relevance":
            return await super().generate_structured(role, _system, user, schema, signal)
        request = json.loads(user)
        return schema.model_validate(
            {
                "judgments": [
                    {
                        "evidence_id": evidence["id"],
                        "relevance": "irrelevant",
                        "subquestion_ids": [],
                        "reason": "Different scientific topic.",
                    }
                    for evidence in request["evidence"]
                ]
            }
        )


class CrossTopicDraftGateway(CrossTopicGateway):
    async def generate_structured(self, role, _system, user, schema, signal):
        if role != "synthesizer":
            return await super().generate_structured(role, _system, user, schema, signal)
        self.roles.append(role)
        return schema.model_validate(
            {
                "answer": (
                    "The paper compares spherical harmonic methods [E2].\n\n"
                    "- Multibeam sonar requires XTF decoding [E2]."
                ),
                "citation_ids": ["E2"],
                "limitations": [],
            }
        )


def test_research_state_machine_rejects_invalid_transition():
    machine = ResearchStateMachine()
    machine.transition("intake")
    machine.transition("planning")

    with pytest.raises(InvalidResearchTransition, match="planning -> synthesis"):
        machine.transition("synthesis")


def test_fallback_plan_splits_a_broad_question_instead_of_reusing_the_full_prompt():
    question = (
        "帮我调研一下，球谐分析最近几年有哪些工作，他们的数据处理过程有哪些，"
        "各种方法有哪些缺陷，还有哪些内外源分离方法，形成一个调研报告"
    )

    plan = _fallback_plan(question, max_subquestions=6, max_queries=6)

    assert len(plan.subquestions) >= 4
    assert all(value.id.startswith("Q") for value in plan.subquestions)
    assert all("球谐分析" in value for value in plan.queries)
    assert question not in plan.queries


def test_evidence_ledger_limits_repeated_chunks_per_item():
    ledger = EvidenceLedger("L", max_chunks_per_item=3)
    ledger.add_local(
        "query",
        [
            hit("c1", score=0.9),
            hit("c2", score=0.8),
            hit("c3", score=0.7),
            hit("c4", score=0.6),
            hit("c5", item="item-2", score=0.5),
        ],
    )

    entries = ledger.entries()

    assert len(entries) == 4
    assert {value.evidence.item_id for value in entries} == {"item-1", "item-2"}
    assert "c4" not in {value.evidence.chunk_id for value in entries}
    assert all(value.level == "fulltext_page" for value in entries)


@pytest.mark.asyncio
async def test_topic_gate_excludes_multibeam_from_spherical_harmonic_answer():
    retrieval = FixtureRetrieval(
        [
            [
                hit(
                    "multibeam",
                    item="sonar",
                    score=0.99,
                    title="Multibeam sonar data preprocessing",
                    text="Ray correction, vessel attitude correction, and XTF decoding.",
                ),
                hit(
                    "spherical",
                    item="sha",
                    score=0.8,
                    title="Spherical harmonic analysis algorithms",
                    text="Comparison of FFT, least squares, and weighted least squares methods.",
                ),
            ]
        ]
    )
    events: list[tuple[str, dict]] = []

    async def sink(event_type, payload):
        events.append((event_type, payload))

    result = await ResearchOrchestrator(
        retrieval,
        CrossTopicGateway(),
        budgets=ResearchBudgets(parallel_scouts=False),
        event_sink=sink,
    ).run("library", "Review spherical harmonic analysis methods", mode="local")

    assert result.citation_ids == ["E2"]
    assert [value.title for value in result.evidence] == ["Spherical harmonic analysis algorithms"]
    excluded = next(value for value in result.all_evidence if value.id == "E1")
    assert excluded.relevance == "irrelevant"
    screening = next(payload for kind, payload in events if kind == "evidence_screened")
    assert screening["counts"]["irrelevant"] == 1


@pytest.mark.asyncio
async def test_topic_contract_removes_cross_topic_content_reintroduced_by_generator():
    result = await ResearchOrchestrator(
        FixtureRetrieval(
            [
                [
                    hit(
                        "multibeam",
                        item="sonar",
                        score=0.99,
                        title="Multibeam sonar data preprocessing",
                        text="Ray correction, vessel attitude correction, and XTF decoding.",
                    ),
                    hit(
                        "spherical",
                        item="sha",
                        score=0.8,
                        title="Spherical harmonic analysis algorithms",
                        text="Comparison of spherical harmonic least-squares methods.",
                    ),
                ]
            ]
        ),
        CrossTopicDraftGateway(),
        budgets=ResearchBudgets(parallel_scouts=False),
    ).run("library", "Review spherical harmonic analysis methods", mode="local")

    assert "spherical harmonic" in result.answer
    assert "Multibeam" not in result.answer
    assert any("multibeam sonar" in value.lower() for value in result.limitations)


@pytest.mark.asyncio
async def test_orchestrator_retrieves_again_when_assessor_reports_a_gap():
    retrieval = FixtureRetrieval([[hit("chunk-1")], [hit("chunk-2", item="item-2")]])
    gateway = FixtureGateway(require_second_round=True)
    events: list[tuple[str, dict]] = []

    async def sink(event_type, payload):
        events.append((event_type, payload))

    result = await ResearchOrchestrator(
        retrieval,
        gateway,
        budgets=ResearchBudgets(max_local_rounds=2, parallel_scouts=False),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    assert result.answer.endswith("[E1].")
    assert result.metrics["local_rounds"] == 2
    assert "independent storm observation" in retrieval.queries
    assert result.coverage[0]["status"] == "covered"
    assert any(event_type == "coverage_updated" for event_type, _ in events)
    assert events[-1][0] == "result_ready"


@pytest.mark.asyncio
async def test_orchestrator_rejects_a_citation_outside_the_ledger():
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        FixtureGateway(invalid_citation=True),
        budgets=ResearchBudgets(parallel_scouts=False),
    )

    with pytest.raises(GenerationError, match="not supplied"):
        await orchestrator.run("library", "What happened?", mode="local")


@pytest.mark.asyncio
async def test_orchestrator_reports_no_evidence_instead_of_generating():
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[]]),
        FixtureGateway(),
        budgets=ResearchBudgets(max_local_rounds=1, parallel_scouts=False),
    )

    with pytest.raises(GenerationError) as caught:
        await orchestrator.run("library", "Unknown topic", mode="local")

    assert caught.value.code == "no_evidence"


@pytest.mark.asyncio
async def test_orchestrator_abstains_when_all_candidates_are_cross_topic():
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval(
            [
                [
                    hit(
                        "multibeam",
                        title="Multibeam sonar data preprocessing",
                        text="XTF decoding and vessel attitude correction.",
                    )
                ]
            ]
        ),
        IrrelevantOnlyGateway(),
        budgets=ResearchBudgets(max_local_rounds=1, parallel_scouts=False),
    )

    with pytest.raises(GenerationError) as caught:
        await orchestrator.run("library", "Spherical harmonic analysis", mode="local")

    assert caught.value.code == "no_relevant_evidence"
    assert "synthesizer" not in orchestrator.gateway.roles


@pytest.mark.asyncio
async def test_optional_scout_is_read_only_and_feeds_the_assessor():
    gateway = FixtureGateway()
    events: list[str] = []

    async def sink(event_type, _payload):
        events.append(event_type)

    result = await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")], [hit("chunk-1")]]),
        gateway,
        budgets=ResearchBudgets(parallel_scouts=True),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    assert result.answer
    assert "scout" in gateway.roles
    assert "scouts_started" in events
    assert "scouts_completed" in events


@pytest.mark.asyncio
async def test_approved_acquisition_is_retrieved_into_the_same_evidence_ledger():
    retrieval = FixtureRetrieval([[hit("new-chunk", item="item-2")]])
    statuses = iter(
        [
            {"decision": "pending", "ready": False},
            {"decision": "approved", "ready": True, "batch_id": "batch-1"},
        ]
    )
    events: list[str] = []

    async def acquisition_source():
        return next(statuses)

    async def sink(event_type, _payload):
        events.append(event_type)

    orchestrator = ResearchOrchestrator(
        retrieval,
        FixtureGateway(),
        budgets=ResearchBudgets(acquisition_wait_seconds=2),
        event_sink=sink,
        acquisition_source=acquisition_source,
    )
    orchestrator.started_at = time.monotonic()
    orchestrator.state.phase = "gap_assessment"
    ledger = EvidenceLedger("E")
    ledger.add_local("initial", [hit("existing-chunk")])

    evidence = await orchestrator._wait_for_acquisition("library", ["newly imported work"], ledger, 20)

    assert {value.chunk_id for value in evidence} == {"existing-chunk", "new-chunk"}
    assert "acquisition_updated" in events
    assert orchestrator.state.phase == "acquisition_wait"
