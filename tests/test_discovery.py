import asyncio
import os

import httpx
import pytest

from researchbrain.discovery.service import (
    ArxivSearchProvider,
    CrossrefSearchProvider,
    DiscoveryRecord,
    LiteratureDiscovery,
    OpenAlexSearchProvider,
    PubMedSearchProvider,
)


@pytest.mark.asyncio
async def test_crossref_discovery_maps_results():
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/discovered",
                    "title": ["Discovered Paper"],
                    "author": [{"given": "Ada", "family": "Lovelace"}],
                    "published": {"date-parts": [[2026]]},
                    "container-title": ["Journal"],
                }
            ]
        }
    }
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        records = await CrossrefSearchProvider("https://api.crossref.org", "test@example.org", client).search(
            "storm", 5
        )
    assert records[0].doi == "10.1000/discovered"
    assert records[0].authors == ["Ada Lovelace"]


@pytest.mark.asyncio
async def test_arxiv_discovery_parses_atom():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/2608.00001v1</id><published>2026-08-01T00:00:00Z</published>
      <title>Example preprint</title><summary>Useful result.</summary>
      <author><name>Ada Lovelace</name></author>
      <arxiv:doi>10.1000/arxiv</arxiv:doi></entry>
    </feed>"""
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text=xml))
    async with httpx.AsyncClient(transport=transport) as client:
        records = await ArxivSearchProvider(client).search("storm", 5)
    assert records[0].source_id == "2608.00001v1"
    assert records[0].year == 2026


@pytest.mark.asyncio
async def test_pubmed_discovery_fetches_abstract_and_identifiers():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID>
    <Article><Journal><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue>
    <Title>Space Weather</Title></Journal><ArticleTitle>Storm response</ArticleTitle>
    <Abstract><AbstractText Label="RESULTS">Observed response.</AbstractText></Abstract>
    <AuthorList><Author><ForeName>Ada</ForeName><LastName>Lovelace</LastName></Author></AuthorList>
    </Article></MedlineCitation><PubmedData><ArticleIdList>
    <ArticleId IdType="doi">10.1000/pubmed</ArticleId><ArticleId IdType="pmc">PMC123</ArticleId>
    </ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""

    def handler(request):
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["123"]}})
        return httpx.Response(200, text=xml)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        records = await PubMedSearchProvider("test@example.org", "key", client).search("storm", 5)

    assert records[0].abstract == "RESULTS: Observed response."
    assert records[0].identifiers == {
        "pmid": "123",
        "doi": "10.1000/pubmed",
        "pmcid": "PMC123",
    }
    assert records[0].is_oa is True


@pytest.mark.asyncio
async def test_openalex_restores_inverted_abstract():
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "display_name": "Merged paper",
                "publication_year": 2025,
                "abstract_inverted_index": {"First": [0], "result": [1]},
                "authorships": [],
                "primary_location": {},
            }
        ]
    }
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        records = await OpenAlexSearchProvider("test@example.org", client=client).search("storm", 5)
    assert records[0].abstract == "First result"


@pytest.mark.asyncio
async def test_discovery_merges_sources_and_reports_provider_failure():
    class GoodProvider:
        name = "good"

        async def search(self, _query, _limit):
            return [
                DiscoveryRecord(
                    source="good",
                    source_id="10.1000/merged",
                    title="Merged paper",
                    authors=[],
                    year=2025,
                    venue="Journal",
                    abstract="",
                    doi="10.1000/merged",
                    url="",
                )
            ]

    class BetterProvider:
        name = "better"

        async def search(self, _query, _limit):
            return [
                DiscoveryRecord(
                    source="better",
                    source_id="W1",
                    title="Merged paper",
                    authors=["Ada Lovelace"],
                    year=2025,
                    venue="",
                    abstract="Longer abstract",
                    doi="10.1000/merged",
                    url="https://example.org",
                )
            ]

    class FailedProvider:
        name = "failed"

        async def search(self, _query, _limit):
            raise RuntimeError("temporary outage")

    discovery = LiteratureDiscovery([GoodProvider(), BetterProvider(), FailedProvider()])
    result = await discovery.search_with_status("storm", 5)

    assert len(result.records) == 1
    assert result.records[0].sources == ["good", "better"]
    assert result.records[0].abstract == "Longer abstract"
    assert result.merge_report[0].canonical_key == "doi:10.1000/merged"
    assert result.merge_report[0].sources == ["good", "better"]
    assert "doi:10.1000/merged" in result.merge_report[0].match_keys
    assert result.providers[-1].status == "failed"
    assert result.providers[-1].error == "temporary outage"


@pytest.mark.asyncio
async def test_discovery_merges_by_pmid_arxiv_and_normalized_title_with_best_record_retained():
    class Provider:
        def __init__(self, name: str, records: list[DiscoveryRecord]):
            self.name = name
            self._records = records

        async def search(self, _query, _limit):
            return self._records

    pubmed_minimal = DiscoveryRecord(
        source="pubmed",
        source_id="123",
        title="Shared PMID paper",
        authors=[],
        year=None,
        venue="",
        abstract="",
        doi="",
        url="",
        identifiers={"pmid": "123"},
    )
    openalex_complete = DiscoveryRecord(
        source="openalex",
        source_id="W1",
        title="Shared PMID paper",
        authors=["Ada Lovelace"],
        year=2026,
        venue="Journal",
        abstract="Detailed traceable abstract.",
        doi="",
        url="https://example.org/pmid",
        identifiers={"pmid": "123", "openalex": "W1"},
    )
    arxiv = DiscoveryRecord(
        source="arxiv",
        source_id="2608.00001v1",
        title="Arxiv duplicate",
        authors=[],
        year=2026,
        venue="arXiv",
        abstract="",
        doi="",
        url="https://arxiv.org/abs/2608.00001v1",
        identifiers={"arxiv": "2608.00001v1"},
    )
    crossref_arxiv = DiscoveryRecord(
        source="crossref",
        source_id="10.1000/arxiv",
        title="Arxiv duplicate",
        authors=[],
        year=2026,
        venue="Journal",
        abstract="Published abstract.",
        doi="10.1000/arxiv",
        url="https://doi.org/10.1000/arxiv",
        identifiers={"arxiv": "2608.00001v1", "doi": "10.1000/arxiv"},
    )
    title_a = DiscoveryRecord(
        source="crossref",
        source_id="title-a",
        title="  Normalized   Title Match ",
        authors=[],
        year=2024,
        venue="",
        abstract="",
        doi="",
        url="",
    )
    title_b = DiscoveryRecord(
        source="openalex",
        source_id="title-b",
        title="Normalized Title Match",
        authors=["Grace Hopper"],
        year=2024,
        venue="Venue",
        abstract="Abstract makes this record more complete.",
        doi="",
        url="https://example.org/title",
    )

    discovery = LiteratureDiscovery(
        [
            Provider("pubmed", [pubmed_minimal]),
            Provider("openalex", [openalex_complete, title_b]),
            Provider("arxiv", [arxiv]),
            Provider("crossref", [crossref_arxiv, title_a]),
        ]
    )
    result = await discovery.search_with_status("topic", 5)

    assert len(result.records) == 3
    by_key = {entry.canonical_key: entry for entry in result.merge_report}
    assert by_key["pmid:123"].merged_count == 2
    assert by_key["pmid:123"].retained_source == "openalex"
    assert by_key["doi:10.1000/arxiv"].merged_count == 2
    assert "arxiv:2608.00001v1" in by_key["doi:10.1000/arxiv"].match_keys
    assert by_key["title:normalized title match"].merged_count == 2
    assert by_key["title:normalized title match"].retained_source == "openalex"


@pytest.mark.asyncio
async def test_discovery_runs_only_selected_sources_and_applies_year_range():
    calls: list[str] = []

    class Provider:
        def __init__(self, name: str, year: int):
            self.name = name
            self.year = year

        async def search(self, _query, _limit):
            calls.append(self.name)
            return [
                DiscoveryRecord(
                    source=self.name,
                    source_id=f"{self.name}-{self.year}",
                    title=f"Paper {self.year}",
                    authors=[],
                    year=self.year,
                    venue="Journal",
                    abstract="Abstract",
                    doi="",
                    url="",
                )
            ]

    discovery = LiteratureDiscovery([Provider("crossref", 2019), Provider("openalex", 2024)])

    result = await discovery.search_with_status(
        "topic",
        5,
        sources=["openalex"],
        year_from=2022,
        year_to=2025,
    )

    assert calls == ["openalex"]
    assert [value.year for value in result.records] == [2024]
    assert [value.source for value in result.providers] == ["openalex"]


@pytest.mark.asyncio
async def test_discovery_combines_relevance_and_time_sorting_with_report():
    class Provider:
        name = "crossref"

        async def search(self, _query, _limit):
            return [
                DiscoveryRecord(
                    source="crossref",
                    source_id="old-relevant",
                    title="Storm model",
                    authors=[],
                    year=2018,
                    venue="Journal",
                    abstract="Storm response model with validation.",
                    doi="",
                    url="",
                ),
                DiscoveryRecord(
                    source="crossref",
                    source_id="new-adjacent",
                    title="Adjacent paper",
                    authors=[],
                    year=2026,
                    venue="Journal",
                    abstract="Unrelated background.",
                    doi="",
                    url="",
                ),
                DiscoveryRecord(
                    source="crossref",
                    source_id="newer-relevant",
                    title="Storm response model",
                    authors=[],
                    year=2025,
                    venue="Journal",
                    abstract="Storm response model.",
                    doi="",
                    url="",
                ),
            ]

    result = await LiteratureDiscovery([Provider()]).search_with_status("storm response model", 5)

    assert [record.source_id for record in result.records] == [
        "newer-relevant",
        "old-relevant",
        "new-adjacent",
    ]
    assert result.ranking_report[0]["sort"] == "relevance_then_time_then_traceability"
    assert result.ranking_report[0]["relevance_score"] >= result.ranking_report[1]["relevance_score"]


@pytest.mark.asyncio
async def test_discovery_provider_retry_reports_degraded_success_and_failed_degradation():
    class FlakyProvider:
        name = "flaky"

        def __init__(self):
            self.calls = 0

        async def search(self, _query, _limit):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("rate limited")
            return [
                DiscoveryRecord(
                    source="flaky",
                    source_id="ok",
                    title="Recovered",
                    authors=[],
                    year=2026,
                    venue="Journal",
                    abstract="Abstract",
                    doi="",
                    url="",
                )
            ]

    class FailedProvider:
        name = "failed"

        async def search(self, _query, _limit):
            raise RuntimeError("still down")

    discovery = LiteratureDiscovery([FlakyProvider(), FailedProvider()], provider_retries=2)
    result = await discovery.search_with_status("topic", 5)

    by_source = {status.source: status for status in result.providers}
    assert by_source["flaky"].status == "degraded"
    assert by_source["flaky"].attempts == 2
    assert by_source["flaky"].degraded_reason == "succeeded after provider-level retry"
    assert by_source["failed"].status == "failed"
    assert by_source["failed"].degraded_reason == "provider unavailable after retries"


@pytest.mark.asyncio
async def test_discovery_provider_timeout_isolated_before_tool_timeout():
    class SlowProvider:
        name = "slow"

        async def search(self, _query, _limit):
            await asyncio.sleep(1)
            return []

    discovery = LiteratureDiscovery(
        [SlowProvider()],
        provider_retries=1,
        provider_timeout_seconds=0.01,
    )
    result = await discovery.search_with_status("topic", 5)

    assert result.records == []
    assert result.providers[0].status == "failed"
    assert result.providers[0].elapsed_ms < 500
    assert result.providers[0].error


@pytest.mark.asyncio
async def test_discovery_expands_seed_references_and_citations():
    seed = DiscoveryRecord(
        source="openalex",
        source_id="W0",
        title="Seed storm model",
        authors=[],
        year=2025,
        venue="Journal",
        abstract="storm model",
        doi="",
        url="",
        identifiers={"openalex": "W0"},
    )
    reference = DiscoveryRecord(
        source="openalex",
        source_id="W-ref",
        title="Reference storm model",
        authors=[],
        year=2020,
        venue="Journal",
        abstract="reference",
        doi="",
        url="",
        identifiers={"openalex": "W-ref"},
    )
    citation = DiscoveryRecord(
        source="openalex",
        source_id="W-cite",
        title="Citing storm model",
        authors=[],
        year=2026,
        venue="Journal",
        abstract="citation",
        doi="",
        url="",
        identifiers={"openalex": "W-cite"},
    )

    class Provider:
        name = "openalex"

        async def search(self, _query, _limit):
            return [seed]

        async def expand_seed(self, record, _limit):
            assert record.source_id == "W0"
            return [reference, citation]

    result = await LiteratureDiscovery([Provider()]).search_with_status(
        "storm model", 5, track_citations=True
    )

    assert {record.source_id for record in result.records} == {"W0", "W-ref", "W-cite"}
    assert result.seed_report[0]["relations"] == ["references", "citations"]
    assert any(status.source == "openalex:seed_expansion" for status in result.providers)


@pytest.mark.asyncio
async def test_optional_real_discovery_smoke():
    if os.environ.get("RESEARCHBRAIN_REAL_DISCOVERY_SMOKE") != "1":
        pytest.skip("set RESEARCHBRAIN_REAL_DISCOVERY_SMOKE=1 to call real metadata services")
    discovery = LiteratureDiscovery(
        [
            CrossrefSearchProvider("https://api.crossref.org", ""),
            OpenAlexSearchProvider(""),
            ArxivSearchProvider(),
            PubMedSearchProvider(""),
        ]
    )
    result = await discovery.search_with_status("spherical harmonic analysis", 1, track_citations=False)
    assert result.providers
    assert any(status.status in {"complete", "degraded"} for status in result.providers)
