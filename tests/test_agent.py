import json

import httpx
import pytest

from researchbrain.agent.deepseek import DeepSeekClient, GenerationError
from researchbrain.agent.service import (
    HISTORY_RETRIEVAL_WEIGHT,
    ConversationTurn,
    Evidence,
    ResearchAgent,
    _build_prompt,
    _weighted_hits,
)
from researchbrain.discovery.service import (
    DiscoveryRecord,
    DiscoverySearchResult,
    ProviderStatus,
)
from researchbrain.retrieval.index import SearchHit


class FixtureRetrieval:
    async def search(self, question, library_id, limit):
        return [
            SearchHit(
                chunk_id="chunk-1",
                item_id="item-1",
                artifact_id="artifact-1",
                title="Storm Paper",
                text="The disturbance lasted two hours.",
                section="Results",
                page_start=3,
                page_end=3,
                score=0.9,
                vector_rank=1,
                keyword_rank=1,
            )
        ]


@pytest.mark.asyncio
async def test_agent_validates_evidence_citations():
    def handler(_request):
        content = json.dumps(
            {
                "answer": "The disturbance lasted two hours [E1].",
                "citation_ids": ["E1"],
                "limitations": [],
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = DeepSeekClient("key", client=client)
        result = await ResearchAgent(FixtureRetrieval(), generator).answer("library", "How long?")
    assert result.citation_ids == ["E1"]
    assert result.evidence[0].page_start == 3


@pytest.mark.asyncio
async def test_agent_rejects_hallucinated_citation():
    def handler(_request):
        content = json.dumps({"answer": "Unsupported [E9].", "citation_ids": ["E9"]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = DeepSeekClient("key", client=client)
        with pytest.raises(GenerationError, match="not supplied"):
            await ResearchAgent(FixtureRetrieval(), generator).answer("library", "How long?")


@pytest.mark.asyncio
async def test_agent_prompt_distinguishes_library_knowledge_from_owned_resources():
    def handler(request):
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        prompt = payload["messages"][1]["content"]
        assert "do NOT prove that the user owns" in system
        assert "hypothesis, minimum required data" in system
        assert "currently selected local literature library only" in prompt
        assert "Evidence level: full-text excerpt" in prompt
        content = json.dumps(
            {
                "answer": "The current library supports a two-hour duration [E1].",
                "citation_ids": ["E1"],
                "limitations": ["The evidence does not establish local data access."],
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = DeepSeekClient("key", client=client)
        result = await ResearchAgent(FixtureRetrieval(), generator).answer(
            "library",
            "What do I have locally and what can I test?",
        )

    assert result.limitations == ["The evidence does not establish local data access."]


def test_prompt_includes_every_selected_evidence_with_bounded_excerpts():
    evidence = [
        Evidence(
            id=f"E{index}",
            chunk_id=f"metadata:item-{index}",
            item_id=f"item-{index}",
            title=f"Paper {index}",
            text="x" * 2500,
            section="题录与摘要",
            page_start=None,
            page_end=None,
            score=1.0,
        )
        for index in range(1, 16)
    ]

    prompt = _build_prompt("Survey the field", evidence)

    assert "[E1] Paper 1" in prompt
    assert "[E15] Paper 15" in prompt
    assert prompt.count("[Evidence excerpt truncated]") == 15


@pytest.mark.asyncio
async def test_online_mode_plans_search_and_uses_web_evidence_without_local_retrieval():
    class ForbiddenRetrieval:
        async def search(self, *_args, **_kwargs):
            raise AssertionError("online mode must not query the local vector index")

    class FixtureDiscovery:
        async def search_with_status(self, _query, _limit):
            return DiscoverySearchResult(
                records=[
                    DiscoveryRecord(
                        source="pubmed",
                        source_id="123",
                        title="Online paper",
                        authors=["Ada Lovelace"],
                        year=2025,
                        venue="Journal",
                        abstract="Online abstract evidence.",
                        doi="10.1000/online",
                        url="https://pubmed.ncbi.nlm.nih.gov/123/",
                    )
                ],
                providers=[ProviderStatus("pubmed", "complete", 1, 10)],
            )

    def handler(request):
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        if "academic-search plan" in system:
            content = json.dumps({"queries": ["geomagnetic storm response"]})
        else:
            assert "[W1] Online paper" in payload["messages"][1]["content"]
            content = json.dumps(
                {
                    "answer": "The online abstract reports evidence [W1].",
                    "citation_ids": ["W1"],
                    "limitations": ["Abstract-level evidence only."],
                }
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = DeepSeekClient("key", client=client)
        result = await ResearchAgent(ForbiddenRetrieval(), generator, FixtureDiscovery()).answer(
            "library", "磁暴研究", mode="online"
        )

    assert result.citation_ids == ["W1"]
    assert result.evidence[0].source_kind == "online"
    assert result.evidence[0].discovery_record["doi"] == "10.1000/online"
    assert result.search_queries == ["磁暴研究", "geomagnetic storm response"]


def test_history_retrieval_uses_lower_weight_than_the_current_question():
    def hit(chunk_id: str, score: float) -> SearchHit:
        return SearchHit(
            chunk_id=chunk_id,
            item_id=f"item-{chunk_id}",
            artifact_id=f"artifact-{chunk_id}",
            title=chunk_id,
            text=chunk_id,
            section="Results",
            page_start=1,
            page_end=1,
            score=score,
            vector_rank=1,
            keyword_rank=1,
        )

    merged = _weighted_hits([hit("current", 0.2)], [hit("history", 0.9)], 10)

    assert HISTORY_RETRIEVAL_WEIGHT == 0.25
    assert [value.chunk_id for value in merged] == ["current", "history"]
    assert merged[0].score == pytest.approx(1 / 61)
    assert merged[1].score == pytest.approx(0.25 / 61)


@pytest.mark.asyncio
async def test_history_is_prompt_continuity_but_not_evidence():
    queries: list[str] = []

    class HistoryRetrieval:
        async def search(self, question, _library_id, _limit):
            queries.append(question)
            suffix = "current" if question == "What method did it use?" else "context"
            return [
                SearchHit(
                    chunk_id=f"chunk-{suffix}",
                    item_id=f"item-{suffix}",
                    artifact_id=f"artifact-{suffix}",
                    title=f"Paper {suffix}",
                    text=f"Evidence {suffix}",
                    section="Methods",
                    page_start=2,
                    page_end=2,
                    score=0.8,
                    vector_rank=1,
                    keyword_rank=1,
                )
            ]

    def handler(request):
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        prompt = payload["messages"][1]["content"]
        assert "zero evidentiary weight" in system
        assert "Conversation continuity (not evidence)" in prompt
        assert "A previous answer that must be rechecked" in prompt
        assert prompt.index("Conversation continuity") < prompt.index("Evidence:")
        content = json.dumps(
            {
                "answer": "The current evidence describes the method [E1].",
                "citation_ids": ["E1"],
                "limitations": [],
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    history = [
        ConversationTurn("user", "Tell me about the earlier study."),
        ConversationTurn("assistant", "A previous answer that must be rechecked."),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = DeepSeekClient("key", client=client)
        result = await ResearchAgent(HistoryRetrieval(), generator).answer(
            "library",
            "What method did it use?",
            conversation_history=history,
        )

    assert len(queries) == 2
    assert "Recent user context" in queries[1]
    assert result.citation_ids == ["E1"]
