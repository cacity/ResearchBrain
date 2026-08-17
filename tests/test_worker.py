import httpx
import pytest

from researchbrain.db.base import Database
from researchbrain.db.models import Job
from researchbrain.documents.parsers import ParsedDocument, normalize_document
from researchbrain.documents.service import DocumentPipeline
from researchbrain.domain import CreatorInput, JobStatus, LibraryMode, ReferenceRecord
from researchbrain.fulltext.discovery import FullTextCandidate
from researchbrain.fulltext.service import FullTextPipeline
from researchbrain.fulltext.storage import ObjectStore
from researchbrain.jobs.service import JobService
from researchbrain.jobs.worker import JobWorker
from researchbrain.library.repository import LibraryRepository
from researchbrain.metadata.crossref import MetadataProviderError
from researchbrain.retrieval.index import LanceIndex
from researchbrain.retrieval.service import EmbeddingPipeline


class FixtureProvider:
    name = "fixture"

    async def resolve_doi(self, doi: str) -> ReferenceRecord:
        return ReferenceRecord(
            title="Resolved Paper",
            year=2026,
            identifiers={"doi": doi},
            creators=[CreatorInput(given="Ada", family="Lovelace")],
        )


class MissingProvider:
    name = "fixture"

    async def resolve_doi(self, doi: str) -> ReferenceRecord:
        raise MetadataProviderError("not_found", f"missing {doi}")


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


class FixtureDocumentParser:
    name = "fixture"
    version = "1"

    async def parse(self, input_path, output_dir):
        markdown = "<!-- page:1 -->\n\nWorker evidence."
        document = normalize_document(
            markdown,
            [{"type": "text", "text": "Worker evidence.", "page_idx": 0}],
            self.name,
            self.version,
        )
        return ParsedDocument(markdown, document, self.name, self.version)


class FixtureEmbedder:
    provider = "fixture"
    model = "fixture-4"
    dimensions = 4

    async def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    async def embed_query(self, text):
        return [1.0, 0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_worker_resolves_metadata_and_queues_fulltext(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Test", LibraryMode.STANDALONE)
        batch = JobService(session).create_doi_batch(library.id, ["10.1000/test"], False)
        batch_id = batch.id

    completed = await JobWorker(database, FixtureProvider()).run_one()

    assert completed is not None
    assert completed.status == JobStatus.COMPLETE.value
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"%PDF-1.7\nworker fixture"))
    async with httpx.AsyncClient(transport=transport) as client:
        fulltext = FullTextPipeline(
            database,
            FixtureFullTextProvider(),
            ObjectStore(settings.data_dir, client=client),
        )
        downloaded = await JobWorker(database, FixtureProvider(), fulltext_pipeline=fulltext).run_one()
    assert downloaded is not None
    assert downloaded.status == JobStatus.COMPLETE.value
    documents = DocumentPipeline(database, settings.data_dir, FixtureDocumentParser())
    parsed = await JobWorker(database, FixtureProvider(), document_pipeline=documents).run_one()
    assert parsed is not None
    assert parsed.status == JobStatus.COMPLETE.value
    embeddings = EmbeddingPipeline(
        database,
        settings.data_dir,
        FixtureEmbedder(),
        LanceIndex(settings.data_dir / "data" / "lancedb", "fixture-4", 4),
    )
    embedding_worker = JobWorker(database, FixtureProvider(), embedding_pipeline=embeddings)
    first_embedding = await embedding_worker.run_one()
    second_embedding = await embedding_worker.run_one()
    assert first_embedding is not None
    assert second_embedding is not None
    assert {first_embedding.job_type, second_embedding.job_type} == {
        "embed_metadata",
        "embed_document",
    }
    assert first_embedding.status == JobStatus.COMPLETE.value
    assert second_embedding.status == JobStatus.COMPLETE.value
    with database.session() as session:
        batch = JobService(session).get_batch(batch_id)
        assert batch is not None
        assert batch.status == "complete"
        assert batch.completed == 1
        jobs = JobService(session).list_jobs()
        assert len(jobs) == 5
        assert session.get(Job, completed.id).result["created"] is True
    database.engine.dispose()


@pytest.mark.asyncio
async def test_worker_records_permanent_metadata_failure(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Test", LibraryMode.STANDALONE)
        batch = JobService(session).create_doi_batch(library.id, ["10.1000/missing"], False)
        batch_id = batch.id

    failed = await JobWorker(database, MissingProvider()).run_one()

    assert failed is not None
    assert failed.status == JobStatus.FAILED.value
    assert failed.error_code == "not_found"
    with database.session() as session:
        batch = JobService(session).get_batch(batch_id)
        assert batch is not None
        assert batch.status == "partial"
        assert batch.failed == 1
    database.engine.dispose()
