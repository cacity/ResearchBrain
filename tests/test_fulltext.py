import hashlib

import httpx
import pytest

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment
from researchbrain.domain import LibraryMode, ReferenceRecord
from researchbrain.fulltext.discovery import (
    FullTextCandidate,
    MultiSourceFullTextProvider,
    OpenAlexFullTextProvider,
    PmcFullTextProvider,
    UnpaywallProvider,
)
from researchbrain.fulltext.service import FullTextPipeline
from researchbrain.fulltext.storage import DownloadError, ObjectStore
from researchbrain.library.repository import LibraryRepository


@pytest.mark.asyncio
async def test_unpaywall_discovery_and_content_addressed_storage(settings):
    pdf = b"%PDF-1.7\nfixture-pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.unpaywall.org":
            return httpx.Response(
                200,
                json={
                    "is_oa": True,
                    "best_oa_location": {
                        "url_for_pdf": "https://repository.example/paper.pdf",
                        "license": "cc-by",
                        "version": "acceptedVersion",
                        "host_type": "repository",
                    },
                    "oa_locations": [],
                },
            )
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = UnpaywallProvider("https://api.unpaywall.org/v2", "test@example.org", client)
        candidates = await provider.discover("10.1000/open")
        stored = await ObjectStore(settings.data_dir, client=client).download_pdf(candidates[0])

    assert stored.sha256 == hashlib.sha256(pdf).hexdigest()
    assert stored.path.exists()
    assert stored.path.read_bytes() == pdf


class FixtureFullTextProvider:
    async def discover(self, doi: str):
        return [
            FullTextCandidate(
                url="https://repository.example/paper.pdf",
                provider="fixture",
                license="cc-by",
                version="acceptedVersion",
            )
        ]


@pytest.mark.asyncio
async def test_fulltext_pipeline_records_attachment(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Test", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(title="Open Paper", identifiers={"doi": "10.1000/open"}),
            "fixture",
        )
        item_id = item.id

    pdf = b"%PDF-1.7\nfixture-pdf"
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=pdf))
    async with httpx.AsyncClient(transport=transport) as client:
        pipeline = FullTextPipeline(
            database,
            FixtureFullTextProvider(),
            ObjectStore(settings.data_dir, client=client),
        )
        result = await pipeline.process(item_id, "10.1000/open", False)

    assert result.attachments_created == 1
    with database.session() as session:
        attachment = session.query(Attachment).filter_by(item_id=item_id).one()
        assert attachment.status == "stored"
        assert attachment.license == "cc-by"
    database.engine.dispose()


@pytest.mark.asyncio
async def test_object_store_rejects_private_ip(settings):
    candidate = FullTextCandidate(
        url="http://127.0.0.1/private.pdf",
        provider="fixture",
        license="cc-by",
        version="publishedVersion",
    )
    with pytest.raises(DownloadError, match="local or private"):
        await ObjectStore(settings.data_dir).download_pdf(candidate)


@pytest.mark.asyncio
async def test_object_store_stream_deduplicates_upload(settings):
    async def chunks():
        yield b"%PDF-1.7\n"
        yield b"manual upload"

    store = ObjectStore(settings.data_dir)
    first = await store.store_pdf_stream(chunks())
    second = await store.store_pdf_stream(chunks())
    assert first.sha256 == second.sha256
    assert first.path == second.path


@pytest.mark.asyncio
async def test_multi_source_discovery_uses_openalex_when_unpaywall_has_no_email():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openalex.org":
            return httpx.Response(
                200,
                json={
                    "best_oa_location": {
                        "is_oa": True,
                        "pdf_url": "https://repository.example/openalex.pdf",
                        "landing_page_url": "https://repository.example/item/1",
                        "license": "cc-by",
                        "version": "acceptedVersion",
                        "source": {"display_name": "Institutional Repository"},
                    },
                    "locations": [],
                },
            )
        if request.url.host == "www.ncbi.nlm.nih.gov":
            return httpx.Response(200, json={"records": []})
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MultiSourceFullTextProvider(
            [
                UnpaywallProvider("https://api.unpaywall.org/v2", "", client),
                OpenAlexFullTextProvider(client=client),
                PmcFullTextProvider(client=client),
            ]
        )
        candidates = await provider.discover("10.1000/openalex")

    assert candidates[0].provider == "openalex"
    assert candidates[0].url == "https://repository.example/openalex.pdf"
    assert candidates[1].access == "landing"


@pytest.mark.asyncio
async def test_fulltext_pipeline_falls_back_to_next_pdf_candidate(settings):
    class FallbackProvider:
        name = "fallback"

        async def discover(self, _doi: str):
            return [
                FullTextCandidate(
                    url="https://repository.example/not-a-pdf",
                    provider="fixture",
                    license="cc-by",
                    version="publishedVersion",
                    priority=1,
                ),
                FullTextCandidate(
                    url="https://repository.example/paper.pdf",
                    provider="fixture",
                    license="cc-by",
                    version="publishedVersion",
                    priority=2,
                ),
            ]

    database, item_id = _fulltext_fixture_database(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("paper.pdf"):
            return httpx.Response(200, content=b"%PDF-1.7\nvalid fallback")
        return httpx.Response(200, content=b"<html>not a pdf</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await FullTextPipeline(
            database,
            FallbackProvider(),
            ObjectStore(settings.data_dir, client=client),
        ).process(item_id, "10.1000/fallback", False)

    assert result.attachments_created == 1
    with database.session() as session:
        attachment = session.query(Attachment).filter_by(item_id=item_id).one()
        assert attachment.source_url == "https://repository.example/paper.pdf"
    database.engine.dispose()


@pytest.mark.asyncio
async def test_fulltext_pipeline_extracts_pdf_from_open_landing_page(settings):
    class LandingProvider:
        name = "landing"

        async def discover(self, _doi: str):
            return [
                FullTextCandidate(
                    url="https://repository.example/item/1",
                    provider="fixture",
                    license="cc-by",
                    version="acceptedVersion",
                    evidence="verified_oa_landing_page",
                    access="landing",
                )
            ]

    database, item_id = _fulltext_fixture_database(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/item/1":
            return httpx.Response(
                200,
                content=(
                    b'<html><head><meta name="citation_pdf_url" content="/files/paper.pdf"></head></html>'
                ),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if request.url.path == "/files/paper.pdf":
            return httpx.Response(200, content=b"%PDF-1.7\nlanding fixture")
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await FullTextPipeline(
            database,
            LandingProvider(),
            ObjectStore(settings.data_dir, client=client),
            landing_client=client,
        ).process(item_id, "10.1000/landing", False)

    assert result.attachments_created == 1
    with database.session() as session:
        attachment = session.query(Attachment).filter_by(item_id=item_id).one()
        assert attachment.source_url == "https://repository.example/files/paper.pdf"
    database.engine.dispose()


def _fulltext_fixture_database(settings) -> tuple[Database, str]:
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Full text", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(title="Fixture", identifiers={"doi": "10.1000/fixture"}),
            "fixture",
        )
        return database, item.id
