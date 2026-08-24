from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from researchbrain.db.models import ImportBatch, Job
from researchbrain.domain import JobStatus, JobType, normalize_doi


class JobService:
    def __init__(self, session: Session):
        self.session = session

    def create_doi_batch(
        self,
        library_id: str,
        raw_dois: list[str],
        include_si: bool,
        collection_id: str | None = None,
    ) -> ImportBatch:
        valid: list[str] = []
        errors: list[dict[str, str | int]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_dois, 1):
            try:
                doi = normalize_doi(raw)
            except ValueError as exc:
                errors.append({"line": index, "input": raw, "error": str(exc)})
                continue
            if doi not in seen:
                seen.add(doi)
                valid.append(doi)

        batch = ImportBatch(
            library_id=library_id,
            source="doi",
            include_si=include_si,
            total=len(valid),
            input_errors=errors,
            status="queued" if valid else "failed",
            finished_at=None if valid else datetime.now(UTC),
        )
        self.session.add(batch)
        self.session.flush()

        for doi in valid:
            digest = hashlib.sha256(f"metadata:{batch.id}:{library_id}:{doi}".encode()).hexdigest()
            payload = {"library_id": library_id, "doi": doi, "include_si": include_si}
            if collection_id:
                payload["collection_id"] = collection_id
            self.session.add(
                Job(
                    batch_id=batch.id,
                    job_type=JobType.RESOLVE_METADATA.value,
                    status=JobStatus.QUEUED.value,
                    idempotency_key=digest,
                    payload=payload,
                )
            )
        self.session.flush()
        return batch

    def get_batch(self, batch_id: str) -> ImportBatch | None:
        return self.session.get(ImportBatch, batch_id)

    def create_zotero_sync_job(self, library_id: str) -> Job:
        active = self.session.scalar(
            select(Job)
            .where(Job.job_type == JobType.ZOTERO_SYNC.value)
            .where(Job.payload["library_id"].as_string() == library_id)
            .where(Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
        )
        if active:
            return active
        digest = hashlib.sha256(f"zotero-sync:{library_id}:{uuid.uuid4()}".encode()).hexdigest()
        job = Job(
            job_type=JobType.ZOTERO_SYNC.value,
            status=JobStatus.QUEUED.value,
            idempotency_key=digest,
            payload={"library_id": library_id},
        )
        self.session.add(job)
        self.session.flush()
        return job

    def retry_job(self, job_id: str) -> Job | None:
        job = self.session.get(Job, job_id)
        if not job:
            return None
        if job.status not in {
            JobStatus.FAILED.value,
            JobStatus.REVIEW_REQUIRED.value,
            JobStatus.CANCELED.value,
        }:
            raise ValueError("job is not retryable")
        job.status = JobStatus.QUEUED.value
        job.progress = 0
        job.attempt = 0
        job.error_code = ""
        job.error_message = ""
        job.started_at = None
        job.finished_at = None
        job.next_retry_at = None
        self.session.flush()
        return job

    def queue_fulltext_job(
        self,
        library_id: str,
        item_id: str,
        doi: str,
        include_si: bool = False,
    ) -> tuple[Job, bool]:
        """Create a full-text job or requeue its terminal idempotent predecessor."""
        job = self.create_fulltext_job(library_id, item_id, doi, include_si)
        if job.status not in {
            JobStatus.COMPLETE.value,
            JobStatus.FAILED.value,
            JobStatus.REVIEW_REQUIRED.value,
            JobStatus.CANCELED.value,
        }:
            return job, False
        job.status = JobStatus.QUEUED.value
        job.progress = 0
        job.attempt = 0
        job.error_code = ""
        job.error_message = ""
        job.started_at = None
        job.finished_at = None
        job.next_retry_at = None
        self.session.flush()
        return job, True

    def retry_failed_jobs(
        self,
        library_id: str | None = None,
        job_types: list[str] | None = None,
        error_codes: list[str] | None = None,
    ) -> int:
        retryable = list(
            self.session.scalars(
                select(Job).where(
                    Job.status.in_(
                        [
                            JobStatus.FAILED.value,
                            JobStatus.REVIEW_REQUIRED.value,
                        ]
                    )
                )
            )
        )
        allowed_types = set(job_types or [])
        allowed_errors = set(error_codes or [])
        count = 0
        for job in retryable:
            if allowed_types and job.job_type not in allowed_types:
                continue
            if allowed_errors and job.error_code not in allowed_errors:
                continue
            if library_id and str(job.payload.get("library_id") or "") != library_id:
                continue
            job.status = JobStatus.QUEUED.value
            job.progress = 0
            job.attempt = 0
            job.error_code = ""
            job.error_message = ""
            job.started_at = None
            job.finished_at = None
            job.next_retry_at = None
            count += 1
        self.session.flush()
        return count

    def create_parse_job(
        self,
        library_id: str,
        item_id: str,
        attachment_id: str,
        source_sha256: str | None = None,
    ) -> Job:
        digest = hashlib.sha256(f"parse:{attachment_id}:{source_sha256 or 'unknown'}".encode()).hexdigest()
        existing = self.session.scalar(select(Job).where(Job.idempotency_key == digest))
        if existing:
            return existing
        job = Job(
            job_type=JobType.PARSE_DOCUMENT.value,
            status=JobStatus.QUEUED.value,
            idempotency_key=digest,
            payload={
                "library_id": library_id,
                "item_id": item_id,
                "attachment_id": attachment_id,
            },
        )
        self.session.add(job)
        self.session.flush()
        return job

    def create_fulltext_job(
        self,
        library_id: str,
        item_id: str,
        doi: str,
        include_si: bool,
    ) -> Job:
        digest = hashlib.sha256(f"fulltext:{library_id}:{item_id}:{doi}".encode()).hexdigest()
        existing = self.session.scalar(select(Job).where(Job.idempotency_key == digest))
        if existing:
            return existing
        job = Job(
            job_type=JobType.RESOLVE_FULLTEXT.value,
            status=JobStatus.QUEUED.value,
            idempotency_key=digest,
            payload={
                "library_id": library_id,
                "item_id": item_id,
                "doi": doi,
                "include_si": include_si,
            },
        )
        self.session.add(job)
        self.session.flush()
        return job

    def create_metadata_embedding_job(self, library_id: str) -> Job:
        active = self.session.scalar(
            select(Job)
            .where(Job.job_type == JobType.EMBED_METADATA.value)
            .where(Job.payload["library_id"].as_string() == library_id)
            .where(Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
        )
        if active:
            return active
        digest = hashlib.sha256(f"embed-metadata:{library_id}:{uuid.uuid4()}".encode()).hexdigest()
        job = Job(
            job_type=JobType.EMBED_METADATA.value,
            status=JobStatus.QUEUED.value,
            idempotency_key=digest,
            payload={"library_id": library_id},
        )
        self.session.add(job)
        self.session.flush()
        return job

    def queue_document_embedding_job(
        self,
        library_id: str,
        item_id: str,
        attachment_id: str,
        artifact_id: str,
        *,
        requeue_terminal: bool = False,
    ) -> tuple[Job, bool]:
        digest = hashlib.sha256(f"embed:{artifact_id}".encode()).hexdigest()
        job = self.session.scalar(select(Job).where(Job.idempotency_key == digest))
        if not job:
            job = Job(
                job_type=JobType.EMBED_DOCUMENT.value,
                status=JobStatus.QUEUED.value,
                idempotency_key=digest,
                payload={
                    "library_id": library_id,
                    "item_id": item_id,
                    "attachment_id": attachment_id,
                    "artifact_id": artifact_id,
                },
            )
            self.session.add(job)
            self.session.flush()
            return job, False
        if requeue_terminal and job.status in {
            JobStatus.COMPLETE.value,
            JobStatus.FAILED.value,
            JobStatus.REVIEW_REQUIRED.value,
            JobStatus.CANCELED.value,
        }:
            job.status = JobStatus.QUEUED.value
            job.progress = 0
            job.attempt = 0
            job.error_code = ""
            job.error_message = ""
            job.started_at = None
            job.finished_at = None
            job.next_retry_at = None
            self.session.flush()
            return job, True
        return job, False

    def list_jobs(self, limit: int = 100) -> list[Job]:
        statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
        return list(self.session.scalars(statement))
