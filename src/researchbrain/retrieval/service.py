from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, select

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, DocumentArtifact, DocumentChunk, Item, ItemEmbedding
from researchbrain.retrieval.chunking import chunk_document
from researchbrain.retrieval.index import LanceIndex, SearchHit


class Embedder(Protocol):
    provider: str
    model: str
    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class EmbeddingResult:
    artifact_id: str
    chunk_count: int
    model: str
    dimensions: int
    reused: bool


@dataclass(frozen=True)
class MetadataEmbeddingResult:
    library_id: str
    indexed: int
    reused: int


class EmbeddingPipeline:
    def __init__(
        self,
        database: Database,
        data_dir: Path,
        embedder: Embedder,
        index: LanceIndex,
        index_version: str = "v1",
        batch_size: int = 64,
    ):
        self.database = database
        self.data_dir = data_dir
        self.embedder = embedder
        self.index = index
        self.index_version = index_version
        self.batch_size = batch_size

    async def process(self, artifact_id: str) -> EmbeddingResult:
        with self.database.session() as session:
            artifact = session.get(DocumentArtifact, artifact_id)
            if not artifact:
                raise ValueError("document artifact not found")
            existing = list(
                session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.artifact_id == artifact_id)
                    .where(DocumentChunk.embedding_model == self.embedder.model)
                    .where(DocumentChunk.index_version == self.index_version)
                    .where(DocumentChunk.index_status == "ready")
                )
            )
            if existing:
                return EmbeddingResult(
                    artifact_id,
                    len(existing),
                    self.embedder.model,
                    self.embedder.dimensions,
                    True,
                )
            attachment = session.get(Attachment, artifact.attachment_id)
            item = session.get(Item, attachment.item_id) if attachment else None
            if not attachment or not item:
                raise ValueError("artifact source item is missing")
            document_path = self.data_dir / artifact.document_json_path
            document = json.loads(document_path.read_text(encoding="utf-8"))
            chunks = chunk_document(artifact.content_hash, document)
            if not chunks:
                raise ValueError("document contains no indexable text")
            metadata = {
                "library_id": item.library_id,
                "item_id": item.id,
                "attachment_id": attachment.id,
                "title": item.title,
                "year": item.year,
            }

        vectors = []
        for start in range(0, len(chunks), self.batch_size):
            batch = chunks[start : start + self.batch_size]
            vectors.extend(await self.embedder.embed_documents([chunk.text for chunk in batch]))
        if len(vectors) != len(chunks):
            raise ValueError("embedder returned an unexpected vector count")

        records = [
            {
                "chunk_id": chunk.id,
                "vector": vector,
                "artifact_id": artifact_id,
                **metadata,
                "text": chunk.text,
                "section": chunk.section,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "content_hash": chunk.content_hash,
                "embedding_provider": self.embedder.provider,
                "embedding_model": self.embedder.model,
                "index_version": self.index_version,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        await asyncio.to_thread(self.index.upsert, artifact_id, records)
        with self.database.session() as session:
            session.execute(delete(DocumentChunk).where(DocumentChunk.artifact_id == artifact_id))
            for chunk in chunks:
                session.add(
                    DocumentChunk(
                        id=chunk.id,
                        artifact_id=artifact_id,
                        item_id=metadata["item_id"],
                        attachment_id=metadata["attachment_id"],
                        ordinal=chunk.ordinal,
                        text=chunk.text,
                        section=chunk.section,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        block_ids=chunk.block_ids,
                        content_hash=chunk.content_hash,
                        embedding_provider=self.embedder.provider,
                        embedding_model=self.embedder.model,
                        embedding_dimensions=self.embedder.dimensions,
                        index_version=self.index_version,
                        index_status="ready",
                    )
                )
        return EmbeddingResult(
            artifact_id,
            len(chunks),
            self.embedder.model,
            self.embedder.dimensions,
            False,
        )

    async def search(self, query: str, library_id: str, limit: int = 10) -> list[SearchHit]:
        await self.ensure_item_metadata(library_id)
        if not self.index.exists():
            return []
        vector = await self.embedder.embed_query(query)
        return await asyncio.to_thread(
            self.index.hybrid_search,
            query,
            vector,
            library_id,
            limit,
        )

    async def ensure_item_metadata(self, library_id: str) -> MetadataEmbeddingResult:
        with self.database.session() as session:
            items = list(
                session.scalars(
                    select(Item)
                    .where(Item.library_id == library_id)
                    .where(Item.status == "active")
                    .order_by(Item.created_at)
                )
            )
            if not items:
                return MetadataEmbeddingResult(library_id, 0, 0)
            item_ids = [item.id for item in items]
            existing = {
                value.item_id: value
                for value in session.scalars(
                    select(ItemEmbedding)
                    .where(ItemEmbedding.item_id.in_(item_ids))
                    .where(ItemEmbedding.embedding_model == self.embedder.model)
                    .where(ItemEmbedding.index_version == self.index_version)
                )
            }
            index_missing = not self.index.exists()
            pending: list[tuple[dict, str]] = []
            for item in items:
                text = _item_metadata_text(item)
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                state = existing.get(item.id)
                if (
                    not index_missing
                    and state
                    and state.content_hash == content_hash
                    and state.index_status == "ready"
                ):
                    continue
                pending.append(
                    (
                        {
                            "chunk_id": f"metadata:{item.id}",
                            "library_id": item.library_id,
                            "item_id": item.id,
                            "artifact_id": f"metadata:{item.id}",
                            "attachment_id": "",
                            "title": item.title,
                            "year": item.year,
                            "text": text,
                            "section": "题录与摘要",
                            "page_start": None,
                            "page_end": None,
                            "content_hash": content_hash,
                            "embedding_provider": self.embedder.provider,
                            "embedding_model": self.embedder.model,
                            "index_version": self.index_version,
                        },
                        content_hash,
                    )
                )

        indexed_records: list[dict] = []
        for start in range(0, len(pending), 32):
            batch = pending[start : start + 32]
            vectors = await self.embedder.embed_documents([record[0]["text"] for record in batch])
            if len(vectors) != len(batch):
                raise ValueError("embedder returned an unexpected vector count")
            indexed_records.extend(
                dict(record, vector=vector) for (record, _), vector in zip(batch, vectors, strict=True)
            )
        if indexed_records:
            await asyncio.to_thread(self.index.upsert_item_metadata, indexed_records)

        if pending:
            with self.database.session() as session:
                states = {
                    value.item_id: value
                    for value in session.scalars(
                        select(ItemEmbedding)
                        .where(ItemEmbedding.item_id.in_([record[0]["item_id"] for record in pending]))
                        .where(ItemEmbedding.embedding_model == self.embedder.model)
                        .where(ItemEmbedding.index_version == self.index_version)
                    )
                }
                for record, content_hash in pending:
                    state = states.get(record["item_id"])
                    if not state:
                        state = ItemEmbedding(
                            item_id=record["item_id"],
                            content_hash=content_hash,
                            embedding_provider=self.embedder.provider,
                            embedding_model=self.embedder.model,
                            embedding_dimensions=self.embedder.dimensions,
                            index_version=self.index_version,
                        )
                        session.add(state)
                    state.content_hash = content_hash
                    state.embedding_provider = self.embedder.provider
                    state.embedding_dimensions = self.embedder.dimensions
                    state.index_status = "ready"

        return MetadataEmbeddingResult(library_id, len(pending), len(items) - len(pending))


def _item_metadata_text(item: Item) -> str:
    fields = [f"Title: {item.title}"]
    if item.abstract:
        fields.append(f"Abstract: {item.abstract[:8000]}")
    if item.container_title:
        fields.append(f"Journal: {item.container_title}")
    if item.year:
        fields.append(f"Year: {item.year}")
    if item.publisher:
        fields.append(f"Publisher: {item.publisher}")
    if item.url:
        fields.append(f"URL: {item.url}")
    return "\n".join(fields)
