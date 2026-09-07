from __future__ import annotations

from typing import Protocol

from sqlalchemy import select

from researchbrain.citations.export import CitationExporter
from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, DocumentArtifact, DocumentChunk, Item, Job
from researchbrain.domain import ReferenceRecord, normalize_doi
from researchbrain.jobs.service import JobService
from researchbrain.orchestration.tools import (
    EmbedDocumentArguments,
    ExportReferencesArguments,
    ImportDoisArguments,
    JobStatusArguments,
    LookupDoiArguments,
    ParsePdfArguments,
    QueueFulltextArguments,
)


class DoiMetadataProvider(Protocol):
    name: str

    async def resolve_doi(self, doi: str) -> ReferenceRecord: ...


class ResearchAcquisitionTools:
    """Database-scoped handlers for Agent DOI, PDF, parsing, and embedding tools."""

    def __init__(self, database: Database, metadata_provider: DoiMetadataProvider):
        self.database = database
        self.metadata_provider = metadata_provider

    async def lookup_doi(self, arguments: LookupDoiArguments) -> dict:
        doi = normalize_doi(arguments.doi)
        record = await self.metadata_provider.resolve_doi(doi)
        return {
            "doi": doi,
            "provider": self.metadata_provider.name,
            "record": record.model_dump(mode="json"),
        }

    async def import_dois(self, arguments: ImportDoisArguments) -> dict:
        dois = list(dict.fromkeys(normalize_doi(value) for value in arguments.dois))
        with self.database.session() as session:
            self._require_library(session, arguments.library_id)
            batch = JobService(session).create_doi_batch(
                arguments.library_id,
                dois,
                arguments.include_si,
            )
            job_ids = list(
                session.scalars(select(Job.id).where(Job.batch_id == batch.id).order_by(Job.created_at))
            )
            return {
                "batch_id": batch.id,
                "status": batch.status,
                "dois": dois,
                "job_ids": job_ids,
                "input_errors": batch.input_errors,
            }

    async def queue_fulltext(self, arguments: QueueFulltextArguments) -> dict:
        with self.database.session() as session:
            item = self._require_item(session, arguments.library_id, arguments.item_id)
            stored_attachments = list(
                session.scalars(
                    select(Attachment).where(
                        Attachment.item_id == item.id,
                        Attachment.status == "stored",
                    )
                )
            )
            if stored_attachments:
                return {
                    "id": "",
                    "job_type": "resolve_fulltext",
                    "status": "complete",
                    "already_complete": True,
                    "attachment_ids": [value.id for value in stored_attachments],
                    "next_action": "parse_pdf",
                    "requeued": False,
                }
            doi = normalize_doi(arguments.doi) if arguments.doi else self._item_doi(session, item.id)
            if not doi:
                raise ValueError("item DOI is required to queue open full text")
            job, requeued = JobService(session).queue_fulltext_job(
                arguments.library_id,
                item.id,
                doi,
                arguments.include_si,
            )
            return self._job_dict(job, requeued=requeued)

    async def job_status(self, arguments: JobStatusArguments) -> dict:
        with self.database.session() as session:
            self._require_library(session, arguments.library_id)
            jobs = list(session.scalars(select(Job).where(Job.id.in_(arguments.job_ids))))
            by_id = {job.id: job for job in jobs}
            missing = [job_id for job_id in arguments.job_ids if job_id not in by_id]
            if missing:
                raise ValueError(f"jobs not found: {', '.join(missing)}")
            for job in jobs:
                if str(job.payload.get("library_id") or "") != arguments.library_id:
                    raise ValueError("job is outside the requested library scope")
            return {
                "jobs": [self._job_dict(by_id[job_id]) for job_id in arguments.job_ids],
                "all_terminal": all(
                    by_id[job_id].status in {"complete", "failed", "review_required", "canceled"}
                    for job_id in arguments.job_ids
                ),
            }

    async def parse_pdf(self, arguments: ParsePdfArguments) -> dict:
        with self.database.session() as session:
            self._require_item(session, arguments.library_id, arguments.item_id)
            attachment = session.get(Attachment, arguments.attachment_id)
            if not attachment or attachment.item_id != arguments.item_id:
                raise ValueError("attachment is outside the requested item scope")
            if attachment.status != "stored":
                raise ValueError("PDF attachment is not stored and ready for parsing")
            artifact = session.scalar(
                select(DocumentArtifact)
                .where(
                    DocumentArtifact.attachment_id == attachment.id,
                    DocumentArtifact.status == "ready",
                )
                .order_by(DocumentArtifact.created_at.desc())
                .limit(1)
            )
            if artifact:
                return {
                    "id": "",
                    "job_type": "parse_document",
                    "status": "complete",
                    "already_complete": True,
                    "result": {"artifact_id": artifact.id, "page_count": artifact.page_count},
                    "next_action": "embed_document",
                    "requeued": False,
                }
            job = JobService(session).create_parse_job(
                arguments.library_id,
                arguments.item_id,
                attachment.id,
                attachment.sha256,
            )
            return self._job_dict(job)

    async def embed_document(self, arguments: EmbedDocumentArguments) -> dict:
        with self.database.session() as session:
            self._require_item(session, arguments.library_id, arguments.item_id)
            artifact = session.get(DocumentArtifact, arguments.artifact_id)
            if not artifact or artifact.attachment_id != arguments.attachment_id:
                raise ValueError("document artifact is outside the requested attachment scope")
            attachment = session.get(Attachment, arguments.attachment_id)
            if not attachment or attachment.item_id != arguments.item_id:
                raise ValueError("attachment is outside the requested item scope")
            if artifact.status != "ready":
                raise ValueError("document artifact is not ready for embedding")
            indexed_chunk = session.scalar(
                select(DocumentChunk.id).where(
                    DocumentChunk.artifact_id == artifact.id,
                    DocumentChunk.index_status == "ready",
                )
            )
            if indexed_chunk:
                return {
                    "id": "",
                    "job_type": "embed_document",
                    "status": "complete",
                    "already_complete": True,
                    "result": {"artifact_id": artifact.id},
                    "next_action": "none",
                    "requeued": False,
                }
            job, requeued = JobService(session).queue_document_embedding_job(
                arguments.library_id,
                arguments.item_id,
                arguments.attachment_id,
                arguments.artifact_id,
                requeue_terminal=True,
            )
            return self._job_dict(job, requeued=requeued)

    async def export_references(self, arguments: ExportReferencesArguments) -> dict:
        with self.database.session() as session:
            items = list(session.scalars(select(Item).where(Item.id.in_(arguments.item_ids))))
            if len(items) != len(set(arguments.item_ids)):
                raise ValueError("one or more references were not found")
            if any(item.library_id != arguments.library_id for item in items):
                raise ValueError("reference is outside the requested library scope")
            artifact = CitationExporter(session).export(arguments.item_ids, arguments.format)
            return {
                "filename": artifact.filename,
                "mime": artifact.mime,
                "content": artifact.content,
                "item_count": len(arguments.item_ids),
            }

    @staticmethod
    def _require_library(session, library_id: str) -> None:
        from researchbrain.db.models import Library

        if not session.get(Library, library_id):
            raise ValueError("library not found")

    @staticmethod
    def _require_item(session, library_id: str, item_id: str) -> Item:
        item = session.get(Item, item_id)
        if not item or item.library_id != library_id or item.status == "tombstone":
            raise ValueError("item not found in requested library")
        return item

    @staticmethod
    def _item_doi(session, item_id: str) -> str:
        from researchbrain.db.models import Identifier

        return str(
            session.scalar(
                select(Identifier.normalized_value)
                .where(Identifier.item_id == item_id, Identifier.scheme == "doi")
                .order_by(Identifier.is_primary.desc())
                .limit(1)
            )
            or ""
        )

    @staticmethod
    def _job_dict(job: Job, *, requeued: bool = False) -> dict:
        return {
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "progress": job.progress,
            "attempt": job.attempt,
            "max_attempts": job.max_attempts,
            "payload": job.payload,
            "result": job.result,
            "error": (
                {"code": job.error_code, "message": job.error_message}
                if job.error_code or job.error_message
                else None
            ),
            "requeued": requeued,
        }
