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
    assert result.providers[-1].status == "failed"
    assert result.providers[-1].error == "temporary outage"
