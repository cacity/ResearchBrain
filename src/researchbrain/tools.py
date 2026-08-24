from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import func, select

from researchbrain.agent.deepseek import DeepSeekClient
from researchbrain.agent.service import ResearchAgent
from researchbrain.citations.export import CitationExporter
from researchbrain.config import Settings, UserConfigStore
from researchbrain.db.base import Database
from researchbrain.db.migrations import upgrade_schema
from researchbrain.db.models import (
    Attachment,
    DocumentArtifact,
    DocumentChunk,
    Identifier,
    Item,
    ItemEmbedding,
    Job,
    Library,
)
from researchbrain.discovery.service import (
    ArxivSearchProvider,
    CrossrefSearchProvider,
    LiteratureDiscovery,
    OpenAlexSearchProvider,
    PubMedSearchProvider,
)
from researchbrain.fulltext.storage import ObjectStore
from researchbrain.jobs.service import JobService
from researchbrain.library.repository import LibraryRepository
from researchbrain.retrieval.index import LanceIndex
from researchbrain.retrieval.minimax import MiniMaxEmbedder
from researchbrain.retrieval.service import EmbeddingPipeline
from researchbrain.secrets import SecretStore


class ResearchBrainTools:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.settings.ensure_directories()
        upgrade_schema(self.settings)
        self.database = Database(self.settings.database_url)
        self.user_config = UserConfigStore(self.settings.data_dir).load()

    def close(self) -> None:
        self.database.engine.dispose()

    def list_libraries(self) -> list[dict]:
        default_library_id = os.getenv("RESEARCHBRAIN_DEFAULT_LIBRARY_ID", "")
        with self.database.session() as session:
            return [
                {
                    "id": library.id,
                    "name": library.name,
                    "mode": library.mode,
                    "last_version": library.last_version,
                    "is_default": library.id == default_library_id,
                }
                for library in LibraryRepository(session).list_libraries()
            ]

    def get_research_context(self) -> dict:
        libraries = self.list_libraries()
        default_library_id = os.getenv("RESEARCHBRAIN_DEFAULT_LIBRARY_ID", "")
        default_library = next(
            (library for library in libraries if library["id"] == default_library_id),
            libraries[0] if len(libraries) == 1 else None,
        )
        return {
            "default_library": default_library,
            "libraries": libraries,
            "data_policy": "ResearchBrain MCP only; the Harness workspace does not expose library files",
        }

    def library_status(self, library_id: str) -> dict:
        with self.database.session() as session:
            library = session.get(Library, library_id)
            if not library:
                raise ValueError("library not found")
            active_items = select(Item.id).where(
                Item.library_id == library_id,
                Item.status != "tombstone",
            )
            item_ids = active_items.subquery()
            return {
                "id": library.id,
                "name": library.name,
                "mode": library.mode,
                "items": session.scalar(select(func.count()).select_from(item_ids)) or 0,
                "pdf_items": session.scalar(
                    select(func.count(func.distinct(Attachment.item_id))).where(
                        Attachment.item_id.in_(select(item_ids.c.id)),
                        Attachment.status == "stored",
                    )
                )
                or 0,
                "parsed_items": session.scalar(
                    select(func.count(func.distinct(Attachment.item_id)))
                    .join(DocumentArtifact, DocumentArtifact.attachment_id == Attachment.id)
                    .where(
                        Attachment.item_id.in_(select(item_ids.c.id)),
                        DocumentArtifact.status == "ready",
                    )
                )
                or 0,
                "fulltext_indexed_items": session.scalar(
                    select(func.count(func.distinct(DocumentChunk.item_id))).where(
                        DocumentChunk.item_id.in_(select(item_ids.c.id)),
                        DocumentChunk.index_status == "ready",
                    )
                )
                or 0,
            }

    def get_item(self, item_id: str) -> dict:
        with self.database.session() as session:
            item = LibraryRepository(session).get_item(item_id)
            if not item:
                raise ValueError("item not found")
            return {
                "id": item.id,
                "library_id": item.library_id,
                "type": item.item_type,
                "title": item.title,
                "abstract": item.abstract,
                "year": item.year,
                "container_title": item.container_title,
                "volume": item.volume,
                "issue": item.issue,
                "pages": item.pages,
                "url": item.url,
                "identifiers": {
                    identifier.scheme: identifier.normalized_value for identifier in item.identifiers
                },
                "creators": [
                    {
                        "given": link.creator.given,
                        "family": link.creator.family,
                        "literal": link.creator.literal,
                        "role": link.role,
                    }
                    for link in sorted(item.creators, key=lambda value: value.position)
                ],
            }

    def item_status(self, item_id: str) -> dict:
        with self.database.session() as session:
            item = session.get(Item, item_id)
            if not item:
                raise ValueError("item not found")
            doi = session.scalar(
                select(Identifier.normalized_value)
                .where(Identifier.item_id == item_id)
                .where(Identifier.scheme == "doi")
                .limit(1)
            )
            pdf_attachment_ids = list(
                session.scalars(
                    select(Attachment.id)
                    .where(Attachment.item_id == item_id)
                    .where(Attachment.status == "stored")
                    .where(
                        (func.lower(Attachment.mime) == "application/pdf")
                        | func.lower(Attachment.logical_name).like("%.pdf")
                        | func.lower(Attachment.object_path).like("%.pdf")
                    )
                )
            )
            artifact_ids = list(
                session.scalars(
                    select(DocumentArtifact.id)
                    .join(Attachment, Attachment.id == DocumentArtifact.attachment_id)
                    .where(Attachment.item_id == item_id)
                    .where(DocumentArtifact.status == "ready")
                )
            )
            metadata_indexed = bool(
                session.scalar(
                    select(ItemEmbedding.id)
                    .where(ItemEmbedding.item_id == item_id)
                    .where(ItemEmbedding.embedding_model == self.settings.minimax_embedding_model)
                    .where(ItemEmbedding.index_version == "v1")
                    .where(ItemEmbedding.index_status == "ready")
                    .limit(1)
                )
            )
            indexed_chunks = (
                session.scalar(
                    select(func.count())
                    .select_from(DocumentChunk)
                    .where(DocumentChunk.item_id == item_id)
                    .where(DocumentChunk.embedding_model == self.settings.minimax_embedding_model)
                    .where(DocumentChunk.index_version == "v1")
                    .where(DocumentChunk.index_status == "ready")
                )
                or 0
            )
            next_actions = []
            if not pdf_attachment_ids and doi:
                next_actions.append("queue_fulltext")
            if not pdf_attachment_ids and not doi:
                next_actions.append("attach_local_pdf")
            if pdf_attachment_ids and not artifact_ids:
                next_actions.append("parse_pdf")
            if artifact_ids and not indexed_chunks:
                next_actions.append("queue_library_index")
            if not metadata_indexed:
                next_actions.append("queue_library_index")
            return {
                "item_id": item.id,
                "library_id": item.library_id,
                "title": item.title,
                "doi": doi,
                "pdf": {
                    "status": "ready" if pdf_attachment_ids else "missing",
                    "count": len(pdf_attachment_ids),
                },
                "parsed": {"status": "ready" if artifact_ids else "missing", "count": len(artifact_ids)},
                "metadata_embedding": {"status": "ready" if metadata_indexed else "missing"},
                "fulltext_embedding": {
                    "status": "ready" if indexed_chunks else "missing",
                    "chunks": indexed_chunks,
                },
                "next_actions": list(dict.fromkeys(next_actions)),
            }

    async def search_library(self, library_id: str, query: str, limit: int = 10) -> list[dict]:
        hits = await self._retrieval().search(query, library_id, max(1, min(limit, 50)))
        return [asdict(hit) for hit in hits]

    async def ask_library(self, library_id: str, question: str, limit: int = 10) -> dict:
        generator = DeepSeekClient(
            SecretStore().get("deepseek_api_key"),
            self.settings.deepseek_base_url,
            self.settings.deepseek_model,
        )
        answer = await ResearchAgent(self._retrieval(), generator).answer(
            library_id,
            question,
            max(1, min(limit, 20)),
        )
        return {
            "answer": answer.answer,
            "citation_ids": answer.citation_ids,
            "evidence": [asdict(value) for value in answer.evidence],
            "limitations": answer.limitations,
            "model": answer.model,
        }

    async def search_online(
        self,
        query: str,
        sources: list[str] | None = None,
        limit_per_source: int = 5,
    ) -> dict:
        contact_email = str(self.user_config.get("contact_email") or self.settings.contact_email)
        providers = {
            "crossref": CrossrefSearchProvider(self.settings.crossref_base_url, contact_email),
            "openalex": OpenAlexSearchProvider(
                contact_email,
                SecretStore().get("openalex_api_key"),
            ),
            "arxiv": ArxivSearchProvider(),
            "pubmed": PubMedSearchProvider(
                contact_email,
                SecretStore().get("ncbi_api_key"),
            ),
        }
        selected_names = list(dict.fromkeys(sources or list(providers)))
        invalid = sorted(set(selected_names) - set(providers))
        if invalid:
            raise ValueError(f"unsupported online sources: {', '.join(invalid)}")
        result = await LiteratureDiscovery([providers[name] for name in selected_names]).search_with_status(
            query,
            max(1, min(limit_per_source, 20)),
        )
        return {
            "records": [asdict(record) for record in result.records],
            "providers": [asdict(status) for status in result.providers],
        }

    def import_dois(self, library_id: str, dois: list[str], include_si: bool = False) -> dict:
        with self.database.session() as session:
            if not session.get(Library, library_id):
                raise ValueError("library not found")
            batch = JobService(session).create_doi_batch(library_id, dois, include_si)
            return {
                "batch_id": batch.id,
                "library_id": batch.library_id,
                "status": batch.status,
                "accepted": batch.total,
                "input_errors": batch.input_errors,
            }

    def sync_zotero(self, library_id: str) -> dict:
        with self.database.session() as session:
            library = session.get(Library, library_id)
            if not library:
                raise ValueError("library not found")
            if library.mode != "zotero_mirror":
                raise ValueError("library is not a Zotero mirror")
            job = JobService(session).create_zotero_sync_job(library_id)
            return {
                "library_id": library.id,
                "library_name": library.name,
                "last_version": library.last_version or 0,
                "job_id": job.id,
                "job_type": job.job_type,
                "status": job.status,
            }

    def queue_library_index(self, library_id: str) -> dict:
        with self.database.session() as session:
            library = session.get(Library, library_id)
            if not library:
                raise ValueError("library not found")
            jobs = JobService(session)
            metadata_job = jobs.create_metadata_embedding_job(library_id)
            artifacts = list(
                session.execute(
                    select(DocumentArtifact, Attachment)
                    .join(Attachment, Attachment.id == DocumentArtifact.attachment_id)
                    .join(Item, Item.id == Attachment.item_id)
                    .where(Item.library_id == library_id)
                    .where(Item.status == "active")
                    .where(DocumentArtifact.status == "ready")
                )
            )
            already_indexed = 0
            document_jobs = []
            requeued = 0
            for artifact, attachment in artifacts:
                indexed = session.scalar(
                    select(DocumentChunk.id)
                    .where(DocumentChunk.artifact_id == artifact.id)
                    .where(DocumentChunk.embedding_model == self.settings.minimax_embedding_model)
                    .where(DocumentChunk.index_version == "v1")
                    .where(DocumentChunk.index_status == "ready")
                    .limit(1)
                )
                if indexed:
                    already_indexed += 1
                    continue
                job, was_requeued = jobs.queue_document_embedding_job(
                    library_id,
                    attachment.item_id,
                    attachment.id,
                    artifact.id,
                    requeue_terminal=True,
                )
                document_jobs.append(job.id)
                requeued += int(was_requeued)
            return {
                "library_id": library.id,
                "library_name": library.name,
                "embedding_model": self.settings.minimax_embedding_model,
                "metadata_job_id": metadata_job.id,
                "metadata_job_status": metadata_job.status,
                "parsed_artifacts": len(artifacts),
                "already_indexed": already_indexed,
                "document_job_ids": document_jobs,
                "document_jobs_pending": len(document_jobs),
                "document_jobs_requeued": requeued,
            }

    def queue_fulltext(self, item_id: str, include_si: bool = False) -> dict:
        with self.database.session() as session:
            item = session.get(Item, item_id)
            if not item:
                raise ValueError("item not found")
            doi = session.scalar(
                select(Identifier.normalized_value)
                .where(Identifier.item_id == item_id)
                .where(Identifier.scheme == "doi")
                .limit(1)
            )
            if not doi:
                raise ValueError("item has no DOI")
            stored_pdf = session.scalar(
                select(Attachment.id)
                .where(Attachment.item_id == item_id)
                .where(Attachment.status == "stored")
                .limit(1)
            )
            if stored_pdf:
                return {
                    "item_id": item_id,
                    "status": "already_available",
                    "attachment_id": stored_pdf,
                }
            job, requeued = JobService(session).queue_fulltext_job(
                item.library_id,
                item.id,
                doi,
                include_si,
            )
            return {
                "item_id": item_id,
                "doi": doi,
                "job_id": job.id,
                "status": job.status,
                "requeued": requeued,
            }

    async def attach_local_pdf(self, item_id: str, pdf_path: str) -> dict:
        source = Path(pdf_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("local PDF not found")
        with self.database.session() as session:
            item = session.get(Item, item_id)
            if not item:
                raise ValueError("item not found")
            library_id = item.library_id

        async def chunks():
            with source.open("rb") as handle:
                while chunk := handle.read(1024 * 128):
                    yield chunk

        stored = await ObjectStore(
            self.settings.data_dir,
            self.settings.max_download_mb,
        ).store_pdf_stream(chunks())
        with self.database.session() as session:
            existing = session.scalar(
                select(Attachment)
                .where(Attachment.item_id == item_id)
                .where(Attachment.sha256 == stored.sha256)
            )
            if existing:
                attachment = existing
                reused = True
            else:
                attachment = Attachment(
                    item_id=item_id,
                    sha256=stored.sha256,
                    logical_name=source.name,
                    object_path=str(stored.path.relative_to(self.settings.data_dir)),
                    mime=stored.mime,
                    source_url="local-file",
                    status="stored",
                    bytes=stored.bytes,
                )
                session.add(attachment)
                session.flush()
                reused = False
            job = JobService(session).create_parse_job(
                library_id,
                item_id,
                attachment.id,
                stored.sha256,
            )
            return {
                "item_id": item_id,
                "attachment_id": attachment.id,
                "sha256": stored.sha256,
                "bytes": stored.bytes,
                "reused": reused,
                "parse_job_id": job.id,
                "parse_job_status": job.status,
            }

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self.database.session() as session:
            jobs = JobService(session).list_jobs(max(1, min(limit, 200)))
            return [_serialize_job(job) for job in jobs]

    def export_references(self, item_ids: list[str], output_format: str) -> dict:
        with self.database.session() as session:
            artifact = CitationExporter(session).export(item_ids, output_format)
            return asdict(artifact)

    def _retrieval(self) -> EmbeddingPipeline:
        embedder = MiniMaxEmbedder(
            SecretStore().get("minimax_api_key"),
            self.settings.minimax_embedding_url,
            str(self.user_config.get("minimax_group_id") or self.settings.minimax_group_id),
            self.settings.minimax_embedding_model,
            self.settings.minimax_embedding_dimensions,
        )
        index = LanceIndex(
            self.settings.data_dir / "data" / "lancedb",
            embedder.model,
            embedder.dimensions,
        )
        return EmbeddingPipeline(self.database, self.settings.data_dir, embedder, index)


def _serialize_job(job: Job) -> dict:
    return {
        "id": job.id,
        "batch_id": job.batch_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "payload": job.payload,
        "result": job.result,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
