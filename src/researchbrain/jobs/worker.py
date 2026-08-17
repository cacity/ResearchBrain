from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, ImportBatch, Job
from researchbrain.documents.parsers import ParserError
from researchbrain.documents.service import DocumentPipeline
from researchbrain.domain import JobStatus, JobType, ReferenceRecord
from researchbrain.fulltext.discovery import FullTextProviderError
from researchbrain.fulltext.service import FullTextPipeline
from researchbrain.fulltext.storage import DownloadError
from researchbrain.jobs.service import JobService
from researchbrain.library.repository import LibraryRepository
from researchbrain.metadata.crossref import MetadataProviderError
from researchbrain.retrieval.minimax import EmbeddingError
from researchbrain.retrieval.service import EmbeddingPipeline
from researchbrain.zotero.attachments import ZoteroAttachmentImporter
from researchbrain.zotero.client import ZoteroConnectionError
from researchbrain.zotero.sync import ZoteroReader, ZoteroSyncService


class DoiMetadataProvider(Protocol):
    name: str

    async def resolve_doi(self, doi: str) -> ReferenceRecord: ...


class JobWorker:
    def __init__(
        self,
        database: Database,
        metadata_provider: DoiMetadataProvider,
        zotero_client: ZoteroReader | None = None,
        fulltext_pipeline: FullTextPipeline | None = None,
        document_pipeline: DocumentPipeline | None = None,
        embedding_pipeline: EmbeddingPipeline | None = None,
        zotero_attachment_importer: ZoteroAttachmentImporter | None = None,
    ):
        self.database = database
        self.metadata_provider = metadata_provider
        self.zotero_client = zotero_client
        self.fulltext_pipeline = fulltext_pipeline
        self.document_pipeline = document_pipeline
        self.embedding_pipeline = embedding_pipeline
        self.zotero_attachment_importer = zotero_attachment_importer

    async def run_one(self) -> Job | None:
        job_id = self._claim_job()
        if not job_id:
            return None

        with self.database.session() as session:
            job = session.get(Job, job_id)
            payload = dict(job.payload) if job else {}
        if not job:
            return None

        if job.job_type == JobType.ZOTERO_SYNC.value:
            return await self._run_zotero_sync(job_id, payload)
        if job.job_type == JobType.RESOLVE_FULLTEXT.value:
            return await self._run_fulltext(job_id, payload)
        if job.job_type == JobType.PARSE_DOCUMENT.value:
            return await self._run_document_parse(job_id, payload)
        if job.job_type == JobType.EMBED_DOCUMENT.value:
            return await self._run_embedding(job_id, payload)
        if job.job_type == JobType.EMBED_METADATA.value:
            return await self._run_metadata_embedding(job_id, payload)
        try:
            record = await self.metadata_provider.resolve_doi(payload["doi"])
            return self._complete_metadata_job(job_id, record)
        except MetadataProviderError as exc:
            return self._fail_or_retry(job_id, exc.code, str(exc), permanent=exc.code == "not_found")
        except Exception as exc:  # Worker boundary: persist unexpected failures for inspection.
            return self._fail_or_retry(job_id, "unexpected_error", str(exc), permanent=False)

    def _claim_job(self) -> str | None:
        now = datetime.now(UTC)
        supported_types = [JobType.RESOLVE_METADATA.value]
        if self.zotero_client:
            supported_types.append(JobType.ZOTERO_SYNC.value)
        if self.fulltext_pipeline:
            supported_types.append(JobType.RESOLVE_FULLTEXT.value)
        if self.document_pipeline:
            supported_types.append(JobType.PARSE_DOCUMENT.value)
        if self.embedding_pipeline:
            supported_types.append(JobType.EMBED_DOCUMENT.value)
            supported_types.append(JobType.EMBED_METADATA.value)
        with self.database.session() as session:
            statement = (
                select(Job)
                .where(Job.job_type.in_(supported_types))
                .where(
                    or_(
                        Job.status == JobStatus.QUEUED.value,
                        (Job.status == JobStatus.RETRY_WAIT.value) & (Job.next_retry_at <= now),
                    )
                )
                .order_by(Job.created_at)
                .limit(1)
            )
            job = session.scalar(statement)
            if not job:
                return None
            job.status = JobStatus.RUNNING.value
            job.started_at = now
            job.attempt += 1
            job.progress = 5
            if job.batch_id:
                batch = session.get(ImportBatch, job.batch_id)
                if batch:
                    batch.status = JobStatus.RUNNING.value
            return job.id

    async def _run_zotero_sync(self, job_id: str, payload: dict) -> Job:
        if not self.zotero_client:
            return self._fail_or_retry(
                job_id,
                "zotero_not_configured",
                "Zotero client is not configured",
                permanent=True,
            )
        try:
            result = await ZoteroSyncService(self.database, self.zotero_client).sync(
                str(payload["library_id"])
            )
            imported = None
            if self.zotero_attachment_importer:
                imported = await self.zotero_attachment_importer.import_pending(str(payload["library_id"]))
            with self.database.session() as session:
                job = session.get(Job, job_id)
                if not job:
                    raise RuntimeError(f"job disappeared: {job_id}")
                job.status = JobStatus.COMPLETE.value
                job.progress = 100
                job.result = {
                    "previous_version": result.previous_version,
                    "library_version": result.library_version,
                    "items_created": result.items_created,
                    "items_updated": result.items_updated,
                    "attachments_linked": result.attachments_linked,
                    "attachments_imported": imported.imported if imported else 0,
                    "attachments_missing": imported.missing if imported else 0,
                    "attachments_invalid": imported.invalid if imported else 0,
                    "tombstones": result.tombstones,
                }
                job.finished_at = datetime.now(UTC)
                return job
        except ZoteroConnectionError as exc:
            return self._fail_or_retry(job_id, "zotero_unavailable", str(exc), permanent=False)
        except ValueError as exc:
            return self._fail_or_retry(job_id, "invalid_zotero_library", str(exc), permanent=True)

    async def _run_fulltext(self, job_id: str, payload: dict) -> Job:
        if not self.fulltext_pipeline:
            return self._fail_or_retry(
                job_id,
                "fulltext_not_configured",
                "Full-text pipeline is not configured",
                permanent=True,
            )
        try:
            result = await self.fulltext_pipeline.process(
                str(payload["item_id"]),
                str(payload["doi"]),
                bool(payload.get("include_si")),
            )
            if not result.attachment_ids:
                return self._mark_review_required(
                    job_id,
                    "no_oa_fulltext",
                    f"No downloadable open PDF among {result.candidates_found} candidates",
                )
            with self.database.session() as session:
                job = session.get(Job, job_id)
                if not job:
                    raise RuntimeError(f"job disappeared: {job_id}")
                for attachment_id in result.attachment_ids:
                    attachment = session.get(Attachment, attachment_id)
                    JobService(session).create_parse_job(
                        str(payload["library_id"]),
                        str(payload["item_id"]),
                        attachment_id,
                        attachment.sha256 if attachment else None,
                    )
                job.status = JobStatus.COMPLETE.value
                job.progress = 100
                job.result = {
                    "attachments_created": result.attachments_created,
                    "attachment_ids": result.attachment_ids,
                    "candidates_found": result.candidates_found,
                }
                job.finished_at = datetime.now(UTC)
                return job
        except FullTextProviderError as exc:
            permanent = exc.code == "contact_email_missing"
            return self._fail_or_retry(job_id, exc.code, str(exc), permanent=permanent)
        except DownloadError as exc:
            permanent = exc.code in {"not_pdf", "too_large", "unsafe_url"}
            return self._fail_or_retry(job_id, exc.code, str(exc), permanent=permanent)
        except ValueError as exc:
            return self._fail_or_retry(job_id, "invalid_fulltext_job", str(exc), permanent=True)

    async def _run_document_parse(self, job_id: str, payload: dict) -> Job:
        if not self.document_pipeline:
            return self._fail_or_retry(
                job_id,
                "document_parser_not_configured",
                "Document parser is not configured",
                permanent=True,
            )
        try:
            result = await self.document_pipeline.process(str(payload["attachment_id"]))
            with self.database.session() as session:
                job = session.get(Job, job_id)
                if not job:
                    raise RuntimeError(f"job disappeared: {job_id}")
                digest = hashlib.sha256(f"embed:{result.artifact_id}".encode()).hexdigest()
                existing = session.scalar(select(Job).where(Job.idempotency_key == digest))
                if not existing:
                    session.add(
                        Job(
                            job_type=JobType.EMBED_DOCUMENT.value,
                            status=JobStatus.QUEUED.value,
                            idempotency_key=digest,
                            payload={
                                "library_id": payload["library_id"],
                                "item_id": payload["item_id"],
                                "attachment_id": payload["attachment_id"],
                                "artifact_id": result.artifact_id,
                            },
                        )
                    )
                job.status = JobStatus.COMPLETE.value
                job.progress = 100
                job.result = {
                    "artifact_id": result.artifact_id,
                    "parser_name": result.parser_name,
                    "parser_version": result.parser_version,
                    "page_count": result.page_count,
                    "reused": result.reused,
                }
                job.finished_at = datetime.now(UTC)
                return job
        except ParserError as exc:
            permanent = exc.code in {"mineru_no_markdown", "mineru_invalid_json"}
            return self._fail_or_retry(job_id, exc.code, str(exc), permanent=permanent)
        except (OSError, ValueError) as exc:
            return self._fail_or_retry(job_id, "document_parse_error", str(exc), permanent=True)

    async def _run_embedding(self, job_id: str, payload: dict) -> Job:
        if not self.embedding_pipeline:
            return self._fail_or_retry(
                job_id,
                "embedding_not_configured",
                "Embedding pipeline is not configured",
                permanent=True,
            )
        try:
            result = await self.embedding_pipeline.process(str(payload["artifact_id"]))
            with self.database.session() as session:
                job = session.get(Job, job_id)
                if not job:
                    raise RuntimeError(f"job disappeared: {job_id}")
                job.status = JobStatus.COMPLETE.value
                job.progress = 100
                job.result = {
                    "artifact_id": result.artifact_id,
                    "chunk_count": result.chunk_count,
                    "model": result.model,
                    "dimensions": result.dimensions,
                    "reused": result.reused,
                }
                job.finished_at = datetime.now(UTC)
                return job
        except EmbeddingError as exc:
            if exc.code in {"api_key_missing", "authentication_failed", "dimension_mismatch"}:
                return self._mark_review_required(job_id, exc.code, str(exc))
            return self._fail_or_retry(job_id, exc.code, str(exc), permanent=False)
        except (OSError, ValueError) as exc:
            return self._fail_or_retry(job_id, "embedding_error", str(exc), permanent=True)

    async def _run_metadata_embedding(self, job_id: str, payload: dict) -> Job:
        if not self.embedding_pipeline:
            return self._fail_or_retry(
                job_id,
                "embedding_not_configured",
                "Embedding pipeline is not configured",
                permanent=True,
            )
        try:
            result = await self.embedding_pipeline.ensure_item_metadata(str(payload["library_id"]))
            with self.database.session() as session:
                job = session.get(Job, job_id)
                if not job:
                    raise RuntimeError(f"job disappeared: {job_id}")
                job.status = JobStatus.COMPLETE.value
                job.progress = 100
                job.result = {"indexed": result.indexed, "reused": result.reused}
                job.finished_at = datetime.now(UTC)
                return job
        except EmbeddingError as exc:
            if exc.code in {"api_key_missing", "authentication_failed", "dimension_mismatch"}:
                return self._mark_review_required(job_id, exc.code, str(exc))
            return self._fail_or_retry(job_id, exc.code, str(exc), permanent=False)
        except (OSError, ValueError) as exc:
            return self._fail_or_retry(job_id, "embedding_error", str(exc), permanent=True)

    def _complete_metadata_job(self, job_id: str, record: ReferenceRecord) -> Job:
        now = datetime.now(UTC)
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if not job:
                raise RuntimeError(f"job disappeared: {job_id}")
            library_id = str(job.payload["library_id"])
            item, created = LibraryRepository(session).add_reference(
                library_id,
                record,
                self.metadata_provider.name,
            )
            doi = record.identifiers.get("doi", str(job.payload["doi"]))
            JobService(session).create_fulltext_job(
                library_id,
                item.id,
                doi,
                bool(job.payload.get("include_si")),
            )
            job.status = JobStatus.COMPLETE.value
            job.progress = 100
            job.result = {"item_id": item.id, "created": created}
            job.finished_at = now
            job.error_code = ""
            job.error_message = ""
            job.next_retry_at = None
            self._refresh_batch(session, job.batch_id)
            session.flush()
            return job

    def _fail_or_retry(self, job_id: str, code: str, message: str, permanent: bool) -> Job:
        now = datetime.now(UTC)
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if not job:
                raise RuntimeError(f"job disappeared: {job_id}")
            job.error_code = code
            job.error_message = message[:4000]
            job.progress = 0
            if permanent or job.attempt >= job.max_attempts:
                job.status = JobStatus.FAILED.value
                job.finished_at = now
                job.next_retry_at = None
            else:
                delay_seconds = min(15 * (2 ** (job.attempt - 1)), 900)
                job.status = JobStatus.RETRY_WAIT.value
                job.next_retry_at = now + timedelta(seconds=delay_seconds)
            self._refresh_batch(session, job.batch_id)
            session.flush()
            return job

    def _mark_review_required(self, job_id: str, code: str, message: str) -> Job:
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if not job:
                raise RuntimeError(f"job disappeared: {job_id}")
            job.status = JobStatus.REVIEW_REQUIRED.value
            job.error_code = code
            job.error_message = message
            job.finished_at = datetime.now(UTC)
            return job

    @staticmethod
    def _refresh_batch(session, batch_id: str | None) -> None:
        if not batch_id:
            return
        batch = session.get(ImportBatch, batch_id)
        if not batch:
            return
        jobs = list(
            session.scalars(
                select(Job)
                .where(Job.batch_id == batch_id)
                .where(Job.job_type == JobType.RESOLVE_METADATA.value)
            )
        )
        was_finished = batch.finished_at is not None
        batch.completed = sum(job.status == JobStatus.COMPLETE.value for job in jobs)
        batch.failed = sum(job.status == JobStatus.FAILED.value for job in jobs)
        if batch.completed + batch.failed >= batch.total:
            batch.status = "complete" if batch.failed == 0 else "partial"
            batch.finished_at = datetime.now(UTC)
            if batch.completed and not was_finished:
                JobService(session).create_metadata_embedding_job(batch.library_id)
        elif any(job.status == JobStatus.RUNNING.value for job in jobs):
            batch.status = JobStatus.RUNNING.value
        else:
            batch.status = JobStatus.QUEUED.value
