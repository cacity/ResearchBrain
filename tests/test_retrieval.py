import json

import httpx
import pytest
from sqlalchemy import select

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, DocumentArtifact, DocumentChunk, ItemEmbedding
from researchbrain.domain import LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository
from researchbrain.retrieval.chunking import chunk_document
from researchbrain.retrieval.index import LanceIndex
from researchbrain.retrieval.minimax import MiniMaxEmbedder
from researchbrain.retrieval.service import EmbeddingPipeline


class FixtureEmbedder:
    provider = "fixture"
    model = "fixture-4"
    dimensions = 4

    def __init__(self):
        self.document_calls = 0

    async def embed_documents(self, texts):
        self.document_calls += 1
        return [self._vector(text) for text in texts]

    async def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        return [1.0, 0.0, 0.0, 0.0] if "storm" in text.lower() else [0.0, 1.0, 0.0, 0.0]


def test_chunk_document_keeps_evidence_location():
    chunks = chunk_document(
        "artifact",
        {
            "blocks": [
                {"id": "b1", "type": "heading", "text": "Results", "page": 2},
                {"id": "b2", "type": "text", "text": "Storm response evidence", "page": 3},
            ]
        },
        max_chars=100,
    )
    assert chunks[0].section == "Results"
    assert chunks[0].page_start == 2
    assert chunks[0].page_end == 3
    assert chunks[0].block_ids == ["b1", "b2"]


@pytest.mark.asyncio
async def test_minimax_uses_asymmetric_embedding_types():
    request_types = []

    def handler(request: httpx.Request):
        payload = json.loads(request.content)
        request_types.append(payload["type"])
        return httpx.Response(200, json={"vectors": [[1.0, 0.0, 0.0, 0.0]], "base_resp": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = MiniMaxEmbedder("key", dimensions=4, client=client)
        await embedder.embed_documents(["document"])
        await embedder.embed_query("query")
    assert request_types == ["db", "query"]


@pytest.mark.asyncio
async def test_embedding_pipeline_indexes_and_searches(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    document = {
        "schema_version": "1",
        "page_count": 1,
        "blocks": [{"id": "b1", "type": "text", "text": "Geomagnetic storm evidence", "page": 1}],
    }
    artifact_path = settings.data_dir / "artifacts" / "fixture" / "document.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(document), encoding="utf-8")
    with database.session() as session:
        library = LibraryRepository(session).create_library("Test", LibraryMode.STANDALONE)
        library_id = library.id
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(title="Storm Paper", identifiers={"doi": "10.1000/storm"}),
            "fixture",
        )
        attachment = Attachment(
            item_id=item.id,
            sha256="a" * 64,
            logical_name="paper.pdf",
            object_path="library/objects/paper.pdf",
            mime="application/pdf",
            status="stored",
        )
        session.add(attachment)
        session.flush()
        artifact = DocumentArtifact(
            attachment_id=attachment.id,
            source_sha256=attachment.sha256,
            parser_name="fixture",
            parser_version="1",
            markdown_path="artifacts/fixture/document.md",
            document_json_path=str(artifact_path.relative_to(settings.data_dir)),
            content_hash="b" * 64,
            page_count=1,
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id

    index = LanceIndex(settings.data_dir / "data" / "lancedb", "fixture-4", 4)
    pipeline = EmbeddingPipeline(database, settings.data_dir, FixtureEmbedder(), index)
    result = await pipeline.process(artifact_id)
    hits = await pipeline.search("storm", library_id)
    assert result.chunk_count == 1
    assert hits[0].title == "Storm Paper"
    assert hits[0].page_start == 1
    with database.session() as session:
        assert session.query(DocumentChunk).count() == 1
    database.engine.dispose()


@pytest.mark.asyncio
async def test_search_indexes_and_reuses_item_abstracts_without_pdfs(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Metadata", LibraryMode.STANDALONE)
        library_id = library.id
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(
                title="May 2024 Geomagnetic Storm",
                abstract="The storm produced large-scale traveling ionospheric disturbances.",
                year=2024,
            ),
            "fixture",
        )
        item_id = item.id

    embedder = FixtureEmbedder()
    pipeline = EmbeddingPipeline(
        database,
        settings.data_dir,
        embedder,
        LanceIndex(settings.data_dir / "data" / "lancedb", embedder.model, embedder.dimensions),
    )
    first = await pipeline.search("storm", library_id)
    second = await pipeline.search("storm", library_id)

    assert first[0].item_id == item_id
    assert first[0].section == "题录与摘要"
    assert first[0].page_start is None
    assert second[0].chunk_id == first[0].chunk_id
    assert embedder.document_calls == 1
    with database.session() as session:
        state = session.scalar(select(ItemEmbedding).where(ItemEmbedding.item_id == item_id))
        assert state is not None
        assert state.index_status == "ready"
    database.engine.dispose()
