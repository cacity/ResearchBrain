import json
import time

import pytest

from researchbrain.agent.deepseek import GenerationError
from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, DocumentArtifact, DocumentChunk
from researchbrain.discovery.service import DiscoveryRecord, DiscoverySearchResult, ProviderStatus
from researchbrain.domain import LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository
from researchbrain.orchestration.evidence import EvidenceLedger
from researchbrain.orchestration.models import (
    EvidenceRelevanceJudgment,
    QuerySpec,
    ResearchBudgets,
    ResearchPlan,
    ResearchSubquestion,
)
from researchbrain.orchestration.orchestrator import ResearchOrchestrator, _fallback_plan, _metadata_matches
from researchbrain.orchestration.state_machine import (
    InvalidResearchTransition,
    ResearchStateMachine,
)
from researchbrain.orchestration.tools import LocalSearchArguments
from researchbrain.retrieval.index import SearchHit


def hit(
    chunk: str,
    item: str = "item-1",
    score: float = 0.9,
    page: int | None = 3,
    title: str | None = None,
    text: str | None = None,
    vector_rank: int | None = 1,
    keyword_rank: int | None = 1,
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
        vector_rank=vector_rank,
        keyword_rank=keyword_rank,
    )


class FixtureRetrieval:
    def __init__(self, results: list[list[SearchHit]]):
        self.results = results
        self.queries: list[str] = []

    async def search(self, query, _library_id, _limit):
        self.queries.append(query)
        index = min(len(self.queries) - 1, len(self.results) - 1)
        return self.results[index]


class DatabaseFixtureRetrieval(FixtureRetrieval):
    def __init__(self, database: Database, results: list[list[SearchHit]] | None = None):
        super().__init__(results or [[]])
        self.database = database


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
        if role == "intake":
            payload = json.loads(user)["deterministic_intent"]
        elif role == "planner":
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
        elif role == "agent_controller":
            payload = json.loads(user)["recommended_action"]
        elif role == "subagent_controller":
            payload = json.loads(user)["recommended_action"]
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


class OnlineFixtureGateway(FixtureGateway):
    async def generate_structured(self, role, system, user, schema, signal):
        if role == "synthesizer":
            self.roles.append(role)
            return schema.model_validate(
                {
                    "answer": "The online literature reports the response [W1].",
                    "citation_ids": ["W1"],
                    "limitations": [],
                }
            )
        if role == "reviewer":
            self.roles.append(role)
            return schema.model_validate(
                {
                    "blocking": [],
                    "warnings": [],
                    "missing_subquestions": [],
                    "valid_citation_ids": ["W1"],
                }
            )
        return await super().generate_structured(role, system, user, schema, signal)


@pytest.mark.asyncio
async def test_online_mode_broadens_across_sources_after_empty_first_round():
    class EmptyThenBroadDiscovery:
        def __init__(self):
            self.calls: list[tuple[str, list[str]]] = []

        async def search_with_status(
            self,
            query,
            _limit,
            sources,
            _year_from,
            _year_to,
            *,
            track_citations,
        ):
            selected = list(sources or [])
            self.calls.append((query, selected))
            if selected:
                return DiscoverySearchResult(
                    records=[],
                    providers=[ProviderStatus(selected[0], "complete", 0, 1)],
                )
            return DiscoverySearchResult(
                records=[
                    DiscoveryRecord(
                        source="crossref",
                        source_id="10.1000/broad",
                        title="Storm response observations",
                        authors=["A. Researcher"],
                        year=2025,
                        venue="Journal",
                        abstract="The study reports a directly observed storm response.",
                        doi="10.1000/broad",
                        url="https://doi.org/10.1000/broad",
                    )
                ],
                providers=[ProviderStatus("crossref", "complete", 1, 1)],
            )

    discovery = EmptyThenBroadDiscovery()
    answer = await ResearchOrchestrator(
        FixtureRetrieval([]),
        OnlineFixtureGateway(),
        discovery=discovery,
        budgets=ResearchBudgets(
            max_online_rounds=2,
            max_queries=3,
            parallel_scouts=False,
        ),
    ).run("library", "What happened during the storm?", mode="online")

    assert answer.citation_ids == ["W1"]
    assert any(not sources for _query, sources in discovery.calls)
    assert len(discovery.calls) > 1


class CoverageDiversityGateway(FixtureGateway):
    async def generate_structured(self, role, _system, user, schema, signal):
        signal.raise_if_cancelled()
        self.roles.append(role)
        if role != "assessor":
            return await super().generate_structured(role, _system, user, schema, signal)
        evidence = json.loads(user)["evidence"]
        payload = {
            "coverage": [
                {
                    "subquestion_id": "Q1",
                    "question": "Which methods should be compared?",
                    "status": "covered",
                    "required_level": "fulltext_page",
                    "evidence_ids": [value["id"] for value in evidence],
                    "missing": [],
                    "next_queries": [],
                }
            ],
            "next_action": "synthesize",
            "additional_queries": [],
            "rationale": "The model treated repeated chunks as sufficient.",
        }
        return schema.model_validate(payload)


class CrossTopicGateway(FixtureGateway):
    async def generate_structured(self, role, _system, user, schema, signal):
        signal.raise_if_cancelled()
        self.roles.append(role)
        if role in {"agent_controller", "subagent_controller"}:
            payload = json.loads(user)["recommended_action"]
        elif role == "intake":
            payload = json.loads(user)["deterministic_intent"]
        elif role == "planner":
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


class MissingInlineCitationGateway(FixtureGateway):
    async def generate_structured(self, role, system, user, schema, signal):
        if role == "synthesizer":
            self.roles.append(role)
            return schema.model_validate(
                {
                    "answer": "The full text reports the storm response.",
                    "citation_ids": ["E1"],
                    "limitations": [],
                }
            )
        if role == "citation_repairer":
            self.roles.append(role)
            return schema.model_validate(
                {
                    "answer": "The full text reports the storm response [E1].",
                    "citation_ids": ["E1"],
                    "limitations": [],
                }
            )
        return await super().generate_structured(role, system, user, schema, signal)


class FailedCitationRepairGateway(MissingInlineCitationGateway):
    async def generate_structured(self, role, system, user, schema, signal):
        if role == "citation_repairer":
            self.roles.append(role)
            raise GenerationError("provider_unavailable", "citation repair unavailable")
        return await super().generate_structured(role, system, user, schema, signal)


class CitationVariantGateway(FixtureGateway):
    async def generate_structured(self, role, system, user, schema, signal):
        if role == "synthesizer":
            self.roles.append(role)
            return schema.model_validate(
                {
                    "answer": "The full text reports the storm response【e1】.",
                    "citation_ids": ["[e1]"],
                    "limitations": [],
                }
            )
        return await super().generate_structured(role, system, user, schema, signal)


class PartiallyUncitedGateway(FixtureGateway):
    async def generate_structured(self, role, system, user, schema, signal):
        if role == "synthesizer":
            self.roles.append(role)
            return schema.model_validate(
                {
                    "answer": (
                        "The evidence reports a storm response [E1]. "
                        "The full text reports the storm response."
                    ),
                    "citation_ids": ["E1"],
                    "limitations": [],
                }
            )
        if role == "citation_repairer":
            self.roles.append(role)
            return schema.model_validate(
                {
                    "answer": (
                        "The evidence reports a storm response [E1]. "
                        "The full text reports the storm response [E1]."
                    ),
                    "citation_ids": ["E1"],
                    "limitations": [],
                }
            )
        return await super().generate_structured(role, system, user, schema, signal)


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


@pytest.mark.asyncio
async def test_local_search_tool_filters_reranks_and_records_optional_metrics():
    metadata = hit(
        "metadata:item-1",
        text="Title: Storm response\nAbstract: storm response overview",
        page=None,
        vector_rank=1,
        keyword_rank=None,
    )
    fulltext = hit(
        "chunk-1",
        text="Full text storm response with decisive method details.",
        page=4,
        vector_rank=None,
        keyword_rank=1,
    )
    excluded = hit("chunk-2", text="Storm response and multibeam sonar should be excluded.")
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[metadata, fulltext, excluded]]),
        FixtureGateway(),
        budgets=ResearchBudgets(
            local_metadata_weight=0.5,
            local_fulltext_weight=2.0,
            parallel_scouts=False,
        ),
    )

    query, hits, metrics = await orchestrator._tool_search_library(
        LocalSearchArguments(
            library_id="library",
            query="storm response",
            limit=5,
            required_terms=["storm response"],
            excluded_terms=["multibeam sonar"],
        )
    )

    assert query == "storm response"
    assert [value.chunk_id for value in hits] == ["chunk-1", "metadata:item-1"]
    assert hits[0].score > hits[1].score
    assert metrics["raw_candidates"] == 3
    assert metrics["term_filtered"] == 2
    assert metrics["metadata_hits"] == 1
    assert metrics["fulltext_hits"] == 1
    assert {"recall_at_k", "mrr", "ndcg"} <= set(metrics)


@pytest.mark.asyncio
async def test_item_and_fulltext_read_tools_return_status_and_complementary_chunks(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Reader", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(
                title="Storm Reading Paper",
                abstract="Structured abstract about storm response.",
                year=2024,
                identifiers={"doi": "10.1000/read"},
            ),
            "fixture",
        )
        attachment = Attachment(
            item_id=item.id,
            sha256="c" * 64,
            logical_name="paper.pdf",
            object_path="objects/paper.pdf",
            mime="application/pdf",
            status="stored",
            bytes=1234,
        )
        session.add(attachment)
        session.flush()
        artifact = DocumentArtifact(
            attachment_id=attachment.id,
            source_sha256=attachment.sha256 or "",
            parser_name="fixture",
            parser_version="1",
            markdown_path="artifacts/read.md",
            document_json_path="artifacts/read.json",
            content_hash="d" * 64,
            page_count=3,
            status="ready",
        )
        session.add(artifact)
        session.flush()
        chunks = [
            DocumentChunk(
                id="read-c1",
                artifact_id=artifact.id,
                item_id=item.id,
                attachment_id=attachment.id,
                ordinal=1,
                text="Storm response introduction.",
                section="Results",
                page_start=1,
                page_end=1,
                block_ids=["b1"],
                content_hash="e" * 64,
                embedding_provider="fixture",
                embedding_model="fixture-4",
                embedding_dimensions=4,
                index_version="v1",
                index_status="ready",
            ),
            DocumentChunk(
                id="read-c2",
                artifact_id=artifact.id,
                item_id=item.id,
                attachment_id=attachment.id,
                ordinal=2,
                text="Detailed storm response method on the next page.",
                section="Results",
                page_start=2,
                page_end=2,
                block_ids=["b2"],
                content_hash="f" * 64,
                embedding_provider="fixture",
                embedding_model="fixture-4",
                embedding_dimensions=4,
                index_version="v1",
                index_status="ready",
            ),
            DocumentChunk(
                id="read-c3",
                artifact_id=artifact.id,
                item_id=item.id,
                attachment_id=attachment.id,
                ordinal=3,
                text="Figure 2 caption: storm response curve near the formula y = ax.",
                section="Figures",
                page_start=None,
                page_end=None,
                block_ids=["b3"],
                content_hash="g" * 64,
                embedding_provider="fixture",
                embedding_model="fixture-4",
                embedding_dimensions=4,
                index_version="v1",
                index_status="ready",
            ),
        ]
        session.add_all(chunks)
        library_id = library.id
        item_id = item.id

    orchestrator = ResearchOrchestrator(DatabaseFixtureRetrieval(database), FixtureGateway())
    [item_result] = await orchestrator.tools.execute_many(
        "get_item",
        [{"library_id": library_id, "item_id": item_id}],
    )
    [read_result] = await orchestrator.tools.execute_many(
        "read_fulltext_chunks",
        [
            {
                "library_id": library_id,
                "item_id": item_id,
                "query": "storm response method",
                "section": "Results",
                "limit": 3,
                "exclude_chunk_ids": ["read-c1"],
            }
        ],
    )
    [figure_result] = await orchestrator.tools.execute_many(
        "read_fulltext_chunks",
        [
            {
                "library_id": library_id,
                "item_id": item_id,
                "query": "storm response curve",
                "target_kind": "figure",
                "limit": 2,
            }
        ],
    )

    assert item_result.succeeded
    assert item_result.value["identifiers"]["doi"] == "10.1000/read"
    assert item_result.value["processing_status"] == {
        "pdf": "ready",
        "parsed": "ready",
        "fulltext_indexed": "ready",
    }
    assert item_result.value["attachments"][0]["status"] == "stored"
    assert read_result.succeeded
    _query, read_hits, metrics = read_result.value
    assert [value.chunk_id for value in read_hits] == ["read-c2"]
    assert metrics["read_scope"]["not_full_document"] is True
    assert metrics["read_scope"]["read_chunk_ids"] == ["read-c2"]
    assert metrics["read_scope"]["unread_ordinal_ranges"] == [{"start": 3, "end": 3}]
    assert figure_result.succeeded
    _query, figure_hits, figure_metrics = figure_result.value
    assert [value.chunk_id for value in figure_hits] == ["read-c3"]
    assert figure_metrics["read_scope"]["target_kind"] == "figure"
    assert any("缺失页码" in value for value in figure_metrics["read_scope"]["limitations"])
    database.engine.dispose()


@pytest.mark.asyncio
async def test_fulltext_read_reports_parse_and_scanned_pdf_limitations(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Failed Parse", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(title="Scanned Paper"),
            "fixture",
        )
        attachment = Attachment(
            item_id=item.id,
            sha256="9" * 64,
            logical_name="scan.pdf",
            mime="application/pdf",
            status="stored",
        )
        session.add(attachment)
        session.flush()
        session.add(
            DocumentArtifact(
                attachment_id=attachment.id,
                source_sha256=attachment.sha256 or "",
                parser_name="fixture",
                parser_version="1",
                markdown_path="artifacts/scan.md",
                document_json_path="artifacts/scan.json",
                content_hash="8" * 64,
                page_count=10,
                status="failed",
            )
        )
        library_id = library.id
        item_id = item.id

    orchestrator = ResearchOrchestrator(DatabaseFixtureRetrieval(database), FixtureGateway())
    [read_result] = await orchestrator.tools.execute_many(
        "read_fulltext_chunks",
        [{"library_id": library_id, "item_id": item_id, "query": "methods", "limit": 2}],
    )

    assert read_result.succeeded
    _query, hits, metrics = read_result.value
    assert hits == []
    limitations = metrics["read_scope"]["limitations"]
    assert any("解析失败" in value for value in limitations)
    assert any("扫描件" in value for value in limitations)
    database.engine.dispose()


@pytest.mark.asyncio
async def test_local_search_reads_multiple_chunks_for_key_fulltext_hit(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Reader Flow", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(title="Storm Flow Paper"),
            "fixture",
        )
        attachment = Attachment(
            item_id=item.id,
            sha256="1" * 64,
            logical_name="flow.pdf",
            mime="application/pdf",
            status="stored",
        )
        session.add(attachment)
        session.flush()
        artifact = DocumentArtifact(
            attachment_id=attachment.id,
            source_sha256=attachment.sha256 or "",
            parser_name="fixture",
            parser_version="1",
            markdown_path="artifacts/flow.md",
            document_json_path="artifacts/flow.json",
            content_hash="2" * 64,
            page_count=2,
        )
        session.add(artifact)
        session.flush()
        session.add_all(
            [
                DocumentChunk(
                    id="flow-c1",
                    artifact_id=artifact.id,
                    item_id=item.id,
                    attachment_id=attachment.id,
                    ordinal=1,
                    text="Storm response first hit.",
                    section="Results",
                    page_start=1,
                    page_end=1,
                    block_ids=[],
                    content_hash="3" * 64,
                    embedding_provider="fixture",
                    embedding_model="fixture-4",
                    embedding_dimensions=4,
                    index_version="v1",
                    index_status="ready",
                ),
                DocumentChunk(
                    id="flow-c2",
                    artifact_id=artifact.id,
                    item_id=item.id,
                    attachment_id=attachment.id,
                    ordinal=2,
                    text="Storm response complementary method detail.",
                    section="Results",
                    page_start=2,
                    page_end=2,
                    block_ids=[],
                    content_hash="4" * 64,
                    embedding_provider="fixture",
                    embedding_model="fixture-4",
                    embedding_dimensions=4,
                    index_version="v1",
                    index_status="ready",
                ),
            ]
        )
        library_id = library.id
        item_id = item.id

    retrieval = DatabaseFixtureRetrieval(database, [[hit("flow-c1", item=item_id, title="Storm Flow Paper")]])
    orchestrator = ResearchOrchestrator(
        retrieval,
        FixtureGateway(),
        budgets=ResearchBudgets(parallel_scouts=False),
    )
    ledger = EvidenceLedger("E", max_chunks_per_item=5)

    await orchestrator._search_local(
        library_id,
        [QuerySpec(id="S1", subquestion_id="Q1", language="en", source="local", query="storm response")],
        ledger,
    )

    chunk_ids = {entry.evidence.chunk_id for entry in ledger.entries()}
    assert {"flow-c1", "flow-c2"} <= chunk_ids
    assert orchestrator.query_diagnostics["S1"]["retrieval_metrics"]["fulltext_reads"]
    database.engine.dispose()


def test_metadata_filter_matches_year_type_author_journal_and_level_arguments():
    arguments = LocalSearchArguments(
        library_id="library",
        query="storm",
        limit=5,
        start_year=2020,
        end_year=2024,
        item_types=["article-journal"],
        authors=["smith"],
        journals=["nature"],
        evidence_levels=["fulltext_page"],
    )

    assert _metadata_matches(
        {
            "year": 2022,
            "item_type": "article-journal",
            "authors": "alice smith",
            "journal": "nature geoscience",
        },
        arguments,
    )
    assert not _metadata_matches(
        {
            "year": 2019,
            "item_type": "article-journal",
            "authors": "alice smith",
            "journal": "nature geoscience",
        },
        arguments,
    )


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
    assert result.plan["research_intent"]["normalized_question"] == "What happened?"
    assert result.plan["query_specs"]
    assert any(event_type == "intent_ready" for event_type, _ in events)
    diagnostics = [payload for event_type, payload in events if event_type == "query_diagnostic"]
    assert diagnostics
    assert diagnostics[-1]["relevant_count"] >= 1
    assert diagnostics[-1]["score_distribution"]
    assert any(event_type == "coverage_updated" for event_type, _ in events)
    assert events[-1][0] == "result_ready"


@pytest.mark.asyncio
async def test_coverage_cannot_be_covered_by_evidence_below_the_required_level():
    metadata_hit = hit(
        "metadata:item-1",
        page=None,
        title="Storm response paper",
        text="Title and bibliographic metadata about storm response only.",
    )

    result = await ResearchOrchestrator(
        FixtureRetrieval([[metadata_hit]]),
        FixtureGateway(),
        budgets=ResearchBudgets(parallel_scouts=False),
    ).run("library", "What happened?", mode="local")

    assert result.coverage[0]["status"] == "partial"
    assert "fulltext_page" in result.coverage[0]["missing"][0]
    assert any("证据仍不充分" in value for value in result.limitations)


@pytest.mark.asyncio
async def test_coverage_detects_single_document_pseudo_coverage_for_comparison_questions():
    ledger = EvidenceLedger()
    ledger.add_local(
        "method comparison",
        [
            hit("chunk-1", item="same-paper"),
            hit("chunk-2", item="same-paper"),
        ],
    )
    ledger.apply_screening(
        [
            EvidenceRelevanceJudgment(
                evidence_id="E1",
                relevance="relevant",
                subquestion_ids=["Q1"],
                reason="same topic",
            ),
            EvidenceRelevanceJudgment(
                evidence_id="E2",
                relevance="relevant",
                subquestion_ids=["Q1"],
                reason="same topic",
            ),
        ]
    )
    plan = ResearchPlan(
        intent="Compare methods",
        subquestions=[
            ResearchSubquestion(
                id="Q1",
                question="Compare method differences and conclusions.",
                type="comparison",
                required_level="fulltext_page",
            )
        ],
        queries=["method comparison"],
    )

    assessment = await ResearchOrchestrator(
        FixtureRetrieval([[]]),
        CoverageDiversityGateway(),
    )._assess(plan, ledger, "local", allow_local=True)

    assert assessment.coverage[0].status == "partial"
    assert any("同一文献" in value for value in assessment.coverage[0].missing)
    assert any("至少两篇独立文献" in value for value in assessment.coverage[0].missing)


@pytest.mark.asyncio
async def test_coverage_gap_records_specific_reason_when_assessor_omits_missing_details():
    plan = ResearchPlan(
        intent="Compare methods",
        subquestions=[
            ResearchSubquestion(
                id="Q1",
                question="Compare method differences and conclusions.",
                type="comparison",
                required_level="fulltext_page",
            )
        ],
        queries=["method comparison"],
    )

    assessment = await ResearchOrchestrator(
        FixtureRetrieval([[]]),
        CoverageDiversityGateway(),
    )._assess(plan, EvidenceLedger(), "local", allow_local=True)

    assert assessment.coverage[0].status == "insufficient_evidence"
    assert assessment.coverage[0].missing == ["缺少可回答 Q1 的同主题证据。"]


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
async def test_orchestrator_repairs_missing_inline_citations_before_review():
    events: list[tuple[str, dict]] = []

    async def sink(event_type, payload):
        events.append((event_type, payload))

    gateway = MissingInlineCitationGateway()
    result = await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        gateway,
        budgets=ResearchBudgets(parallel_scouts=False),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    assert result.answer.endswith("[E1].")
    assert result.citation_ids == ["E1"]
    assert "citation_repairer" in gateway.roles
    assert any(kind == "citation_repair_completed" for kind, _payload in events)


@pytest.mark.asyncio
async def test_orchestrator_normalizes_common_citation_bracket_variants():
    gateway = CitationVariantGateway()
    result = await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        gateway,
        budgets=ResearchBudgets(parallel_scouts=False),
    ).run("library", "What happened?", mode="local")

    assert "[E1]" in result.answer
    assert result.citation_ids == ["E1"]
    assert "citation_repairer" not in gateway.roles


@pytest.mark.asyncio
async def test_orchestrator_repairs_partially_uncited_factual_claims_before_review():
    gateway = PartiallyUncitedGateway()
    result = await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        gateway,
        budgets=ResearchBudgets(parallel_scouts=False),
    ).run("library", "What happened?", mode="local")

    assert result.answer.count("[E1]") == 2
    assert "citation_repairer" in gateway.roles


@pytest.mark.asyncio
async def test_failed_citation_repair_returns_a_cited_evidence_catalog():
    events: list[tuple[str, dict]] = []

    async def sink(event_type, payload):
        events.append((event_type, payload))

    result = await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        FailedCitationRepairGateway(),
        budgets=ResearchBudgets(parallel_scouts=False),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    assert "已筛选证据目录" in result.answer
    assert "[E1]" in result.answer
    assert result.citation_ids == ["E1"]
    assert any("未能完成逐项引用绑定" in value for value in result.limitations)
    assert any(kind == "citation_repair_degraded" for kind, _payload in events)


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_orchestrator_stops_after_two_query_rewrites_without_relevant_gain():
    retrieval = FixtureRetrieval([[], [], []])
    orchestrator = ResearchOrchestrator(
        retrieval,
        FixtureGateway(require_second_round=True),
        budgets=ResearchBudgets(max_local_rounds=3, parallel_scouts=False),
    )

    with pytest.raises(GenerationError) as caught:
        await orchestrator.run("library", "What happened?", mode="local")

    assert caught.value.code == "no_evidence"
    assert orchestrator.gateway.assessments == 2
    assert any("连续两轮" in value for value in orchestrator.limitations)


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
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
@pytest.mark.research_subagent_loop
async def test_optional_scout_is_read_only_and_feeds_the_assessor():
    gateway = FixtureGateway()
    events: list[tuple[str, dict]] = []

    async def sink(event_type, payload):
        events.append((event_type, payload))

    result = await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")], [hit("subagent-extra", item="item-2")]]),
        gateway,
        budgets=ResearchBudgets(parallel_scouts=True),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    assert result.answer
    assert "scout" in gateway.roles
    event_types = [event_type for event_type, _payload in events]
    assert "scouts_started" in event_types
    assert "subagent_turn_started" in event_types
    assert "subagent_turn_completed" in event_types
    assert "scouts_completed" in event_types
    started = next(payload for event_type, payload in events if event_type == "scouts_started")
    assert started["tasks"][0]["allowed_tools"] == ["read_fulltext_chunks", "search_library"]
    completed = next(payload for event_type, payload in events if event_type == "scouts_completed")
    assert completed["total_tool_calls"] >= 1
    assert result.metrics["scout_findings"]


@pytest.mark.research_subagent_loop
def test_subagent_tasks_have_budgets_context_limits_and_nonduplicated_online_sources():
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        FixtureGateway(),
        discovery=object(),
        budgets=ResearchBudgets(parallel_scouts=True),
    )
    plan = ResearchPlan(
        intent="compare sources",
        subquestions=[
            ResearchSubquestion(id="Q1", question="What did source one report?"),
            ResearchSubquestion(id="Q2", question="What did source two report?"),
        ],
        queries=["fallback"],
        query_specs=[
            QuerySpec(id="S1", subquestion_id="Q1", language="en", source="openalex", query="alpha"),
            QuerySpec(id="S2", subquestion_id="Q2", language="en", source="openalex", query="beta"),
            QuerySpec(id="S3", subquestion_id="Q2", language="en", source="crossref", query="beta"),
        ],
    )

    tasks = orchestrator._build_subagent_tasks(plan)

    assert [task.id for task in tasks] == ["SA1", "SA2"]
    assert all(task.budget.max_steps == 2 for task in tasks)
    assert all(task.budget.context_evidence_limit == 8 for task in tasks)
    assert tasks[0].allowed_tools == ["read_fulltext_chunks", "search_online"]
    assert tasks[0].assigned_sources == ["openalex"]
    assert tasks[1].assigned_sources == ["crossref"]


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
    assert "action_turn_resumed" in events
    assert orchestrator.state.phase == "acquisition_wait"
