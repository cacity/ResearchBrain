import json

import pytest

from researchbrain.agent.gateway import CancellationSignal
from researchbrain.orchestration.models import ResearchBudgets
from researchbrain.orchestration.orchestrator import ResearchOrchestrator
from researchbrain.retrieval.index import SearchHit


class FixtureRetrieval:
    async def search(self, query, _library_id, _limit):
        return [
            SearchHit(
                chunk_id="chunk-1",
                item_id="item-1",
                artifact_id="artifact-1",
                title="Spherical harmonic analysis paper",
                text=f"Full text evidence about {query} and spherical harmonic analysis.",
                section="Methods",
                page_start=2,
                page_end=2,
                score=0.9,
                vector_rank=1,
                keyword_rank=1,
            )
        ]


class ClarificationGateway:
    model = "clarification-fixture"

    def __init__(self):
        self.planner_user_payload = {}

    async def generate_structured(self, role, _system, user, schema, signal):
        signal.raise_if_cancelled()
        if role == "intake":
            deterministic = json.loads(user)["deterministic_intent"]
            payload = {
                **deterministic,
                "ambiguities": ["请确认这里的分析对象是球谐分析还是多波束声呐处理？"],
                "clarification_required": True,
            }
        elif role == "planner":
            self.planner_user_payload = json.loads(user)
            assert "用户澄清" in self.planner_user_payload["question"]
            assert self.planner_user_payload["research_intent"]["clarification_required"] is False
            payload = {
                "intent": "Clarified spherical harmonic analysis scope",
                "subquestions": [
                    {
                        "id": "Q1",
                        "question": "What does the full text report about spherical harmonic analysis?",
                        "type": "method",
                        "required_level": "fulltext_page",
                    }
                ],
                "queries": ["spherical harmonic analysis"],
                "topic_terms": ["spherical harmonic analysis"],
                "excluded_terms": ["multibeam sonar"],
                "completion_criteria": ["Q1 is supported by full text"],
            }
        elif role == "agent_controller":
            payload = json.loads(user)["recommended_action"]
        elif role == "subagent_controller":
            payload = json.loads(user)["recommended_action"]
        elif role == "relevance":
            request = json.loads(user)
            payload = {
                "judgments": [
                    {
                        "evidence_id": evidence["id"],
                        "relevance": "relevant",
                        "subquestion_ids": ["Q1"],
                        "reason": "The evidence matches the clarified spherical harmonic analysis scope.",
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
                        "question": "What does the full text report about spherical harmonic analysis?",
                        "status": "covered",
                        "required_level": "fulltext_page",
                        "evidence_ids": [evidence[0]["id"]] if evidence else [],
                        "missing": [],
                        "next_queries": [],
                    }
                ],
                "next_action": "synthesize",
                "additional_queries": [],
                "rationale": "Clarified evidence is sufficient.",
            }
        elif role == "synthesizer":
            payload = {
                "answer": "The clarified full text supports spherical harmonic analysis [E1].",
                "citation_ids": ["E1"],
                "limitations": [],
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


class OverClarifyingGateway:
    model = "over-clarifying-fixture"

    async def generate_structured(self, role, _system, user, schema, signal):
        signal.raise_if_cancelled()
        assert role == "intake"
        deterministic = json.loads(user)["deterministic_intent"]
        return schema.model_validate(
            {
                **deterministic,
                "domains": ["地球物理学"],
                "research_objects": ["地磁场"],
                "assumptions": [
                    "球谐分析主要用于地球物理场建模。",
                    "内外源分离指地磁场内部源与外部源分离。",
                ],
                "ambiguities": [
                    "请确认球谐分析是否指地磁方法，还是多波束声呐处理。",
                    "内外源分离的具体对象尚未进一步限定。",
                    "调研报告的篇幅与颗粒度未指定。",
                ],
                "clarification_required": True,
            }
        )


@pytest.mark.research_clarification
@pytest.mark.research_agent_action_loop
@pytest.mark.asyncio
async def test_blocking_ambiguity_enters_ask_user_and_resumes_same_run():
    events: list[tuple[str, dict]] = []
    steering_messages = [[{"kind": "clarification", "content": "对象是球谐分析，不是多波束声呐。"}]]

    async def sink(kind, payload):
        events.append((kind, payload))

    async def steering_source():
        return steering_messages.pop(0) if steering_messages else []

    gateway = ClarificationGateway()
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval(),
        gateway,
        budgets=ResearchBudgets(acquisition_wait_seconds=1),
        event_sink=sink,
        signal=CancellationSignal(),
        steering_source=steering_source,
    )

    answer = await orchestrator.run("lib-1", "分析这个主题", mode="local")

    event_types = [kind for kind, _ in events]
    assert "ask_user" in event_types
    assert "clarification_received" in event_types
    assert event_types.index("ask_user") < event_types.index("clarification_received")
    assert event_types.index("clarification_received") < event_types.index("plan_ready")
    assert any(
        kind == "stop_decision" and payload["reason"] == "waiting_for_clarification"
        for kind, payload in events
    )
    assert answer.citation_ids == ["E1"]
    assert "对象是球谐分析" in gateway.planner_user_payload["question"]


@pytest.mark.research_clarification
@pytest.mark.asyncio
async def test_explicit_spherical_harmonic_topic_does_not_block_on_optional_scope_preferences():
    question = (
        "帮我调研一下，球谐分析最近几年有那些人做了这方面的工作，他们数据处理过程有那些，"
        "有那些注意事项，各种方法有那些缺陷，存在的问题，关于数据，需要准备什么样的数据，"
        "还有那些内外源分离的方法，形成一个调研报告"
    )
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval(),
        OverClarifyingGateway(),
        signal=CancellationSignal(),
    )

    intent = await orchestrator._understand(question, [], {})

    assert intent.clarification_required is False
    assert intent.ambiguities == []
    assert "球谐分析" in intent.methods
    assert any("地球物理场建模" in value for value in intent.assumptions)
    assert any("合理默认范围" in value for value in intent.assumptions)
    assert not any("多波束" in value for value in intent.assumptions)
