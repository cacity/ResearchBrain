import httpx
import pytest

from researchbrain.metadata.crossref import CrossrefProvider


@pytest.mark.asyncio
async def test_crossref_provider_maps_record():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.split(b"?", 1)[0]
        assert path.endswith(b"/works/10.1000%2Ftest")
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.1000/Test",
                    "type": "journal-article",
                    "title": ["A Test Paper"],
                    "abstract": "<jats:p>Useful abstract.</jats:p>",
                    "published-online": {"date-parts": [[2026, 8, 1]]},
                    "container-title": ["Journal of Tests"],
                    "author": [{"given": "Ada", "family": "Lovelace"}],
                    "URL": "https://doi.org/10.1000/test",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        record = await CrossrefProvider("https://api.crossref.org", "test@example.org", client).resolve_doi(
            "10.1000/test"
        )

    assert record.title == "A Test Paper"
    assert record.abstract == "Useful abstract."
    assert record.year == 2026
    assert record.identifiers["doi"] == "10.1000/test"
    assert record.creators[0].family == "Lovelace"
