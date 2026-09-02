import asyncio
import json
import logging
import os
import shutil
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchbrain import __version__
from researchbrain.agent.deepseek import DeepSeekClient, GenerationError
from researchbrain.agent.gateway import CancellationSignal, DeepSeekGateway
from researchbrain.agent.service import AgentAnswer, ConversationTurn, ResearchAgent
from researchbrain.citations.export import CitationExporter, CitationExportError
from researchbrain.config import Settings, UserConfigStore
from researchbrain.db.base import Database
from researchbrain.db.migrations import upgrade_schema
from researchbrain.db.models import (
    Attachment,
    ChatMessage,
    ChatSession,
    DocumentArtifact,
    DocumentChunk,
    Identifier,
    ImportBatch,
    Item,
    ItemEmbedding,
    Job,
    Library,
    ResearchRun,
)
from researchbrain.discovery.service import (
    ArxivSearchProvider,
    CrossrefSearchProvider,
    LiteratureDiscovery,
    OpenAlexSearchProvider,
    PubMedSearchProvider,
)
from researchbrain.documents.parsers import FallbackParser, MinerUParser, PyMuPDFParser
from researchbrain.documents.service import DocumentPipeline
from researchbrain.domain import (
    CreatorInput,
    DoiImportRequest,
    JobStatus,
    JobType,
    LibraryCreateRequest,
    ReferenceRecord,
    normalize_doi,
)
from researchbrain.fulltext.discovery import (
    MultiSourceFullTextProvider,
    OpenAlexFullTextProvider,
    PmcFullTextProvider,
    UnpaywallProvider,
)
from researchbrain.fulltext.service import FullTextPipeline
from researchbrain.fulltext.storage import DownloadError, ObjectStore
from researchbrain.harness import HarnessInstallError, HarnessRuntimeManager
from researchbrain.jobs.service import JobService
from researchbrain.jobs.worker import JobWorker
from researchbrain.library.repository import LibraryRepository
from researchbrain.lifecycle import exit_when_parent_stops
from researchbrain.metadata.crossref import CrossrefProvider
from researchbrain.orchestration import ResearchBudgets, ResearchOrchestrator
from researchbrain.orchestration.store import TERMINAL_RUN_STATUSES, ResearchRunStore
from researchbrain.retrieval.index import LanceIndex
from researchbrain.retrieval.minimax import EmbeddingError, MiniMaxEmbedder
from researchbrain.retrieval.service import EmbeddingPipeline
from researchbrain.runtime.manager import RuntimeInstallError, RuntimeManager
from researchbrain.secrets import SecretStore, SecretStoreError
from researchbrain.skills import SkillError
from researchbrain.zotero.attachments import ZoteroAttachmentImporter
from researchbrain.zotero.client import ZoteroConnectionError, ZoteroLocalClient

logger = logging.getLogger(__name__)


class AppState:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings.database_url)
        self.session_token = os.getenv("RESEARCHBRAIN_SESSION_TOKEN", "")
        self.parent_pid = int(os.getenv("RESEARCHBRAIN_PARENT_PID", "0") or 0)
        self.user_config = UserConfigStore(settings.data_dir)
        saved = self.user_config.load()
        self.contact_email = str(saved.get("contact_email") or settings.contact_email)
        self.minimax_group_id = str(saved.get("minimax_group_id") or settings.minimax_group_id)
        self.zotero_data_dir = Path(saved.get("zotero_data_dir") or settings.zotero_data_dir)
        self.mineru_executable = str(saved.get("mineru_executable") or settings.mineru_executable)
        self.harness_port = int(saved.get("harness_port") or settings.harness_port)
        self.harness = HarnessRuntimeManager(settings.data_dir)
        self.research_tasks: dict[str, asyncio.Task] = {}
        self.research_signals: dict[str, CancellationSignal] = {}
        self.research_steering: dict[str, list[dict[str, str]]] = {}
        self.research_event_locks: dict[str, asyncio.Lock] = {}
        self.shutting_down = False


class LibraryResponse(BaseModel):
    id: str
    name: str
    mode: str
    last_version: int | None


class BatchResponse(BaseModel):
    id: str
    library_id: str
    status: str
    total: int
    completed: int
    failed: int
    include_si: bool
    input_errors: list


class SearchRequest(BaseModel):
    library_id: str
    query: str
    limit: int = 10


class ChatSessionCreateRequest(BaseModel):
    library_id: str
    title: str = "New research"


class ChatMessageRequest(BaseModel):
    content: str
    evidence_limit: int = 15
    mode: Literal["local", "hybrid", "online"] = "local"


class ResearchRunRequest(ChatMessageRequest):
    budgets: ResearchBudgets | None = None


class ResearchSteerRequest(BaseModel):
    kind: Literal["constraint", "follow_up"] = "constraint"
    content: str = Field(min_length=1, max_length=2000)


class ExportRequest(BaseModel):
    item_ids: list[str]
    format: Literal["csl-json", "bibtex", "ris", "doi", "markdown"]


class DiscoverySearchRequest(BaseModel):
    query: str
    limit_per_source: int = 10
    sources: list[Literal["crossref", "openalex", "arxiv", "pubmed"]] = Field(
        default_factory=lambda: ["crossref", "openalex", "arxiv", "pubmed"]
    )
    year_from: int | None = None
    year_to: int | None = None
    oa_only: bool = False


class DiscoveryRecordInput(BaseModel):
    source: str
    source_id: str = ""
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    doi: str = ""
    url: str = ""
    sources: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    is_oa: bool = False
    fulltext_url: str = ""
    publication_type: str = "article-journal"


class DiscoveryImportRequest(BaseModel):
    library_id: str
    records: list[DiscoveryRecordInput] = Field(min_length=1, max_length=500)
    include_si: bool = False


class RuntimeInstallRequest(BaseModel):
    name: str
    version: str
    archive_path: str
    sha256: str


class HarnessActionRequest(BaseModel):
    library_id: str = ""
    port: int = Field(default=3080, ge=1024, le=65535)


class SkillInstallRequest(BaseModel):
    source_kind: Literal["local", "archive", "github"]
    source: str = Field(min_length=1, max_length=2048)
    ref: str = Field(default="", max_length=255)
    subpath: str = Field(default="", max_length=1024)
    enabled: bool = False


class SkillEnableRequest(BaseModel):
    enabled: bool


class SkillLaunchRequest(BaseModel):
    library_id: str
    port: int = Field(default=3080, ge=1024, le=65535)


class PublicConfigUpdateRequest(BaseModel):
    contact_email: str | None = None
    minimax_group_id: str | None = None
    zotero_data_dir: str | None = None
    mineru_executable: str | None = None


class CredentialUpdateRequest(BaseModel):
    name: Literal[
        "minimax_api_key",
        "deepseek_api_key",
        "ncbi_api_key",
        "openalex_api_key",
    ]
    value: str


class BulkRetryRequest(BaseModel):
    library_id: str | None = None
    job_types: list[str] = Field(default_factory=list)


class LiteratureLookupRequest(BaseModel):
    doi: str
    pdf_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


def _looks_like_pdf(mime: str, logical_name: str, object_path: str) -> bool:
    return (
        mime.lower() == "application/pdf"
        or logical_name.lower().endswith(".pdf")
        or object_path.lower().endswith(".pdf")
    )


def _safe_data_path(data_dir: Path, stored_path: str) -> Path:
    root = data_dir.resolve()
    candidate = Path(stored_path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="file path is outside the data directory") from exc
    return resolved


def _run_library_id(database: Database, chat_session_id: str) -> str:
    with database.session() as session:
        chat_session = session.get(ChatSession, chat_session_id)
        if not chat_session:
            raise ValueError("chat session not found")
        return chat_session.library_id


def _item_pipeline_statuses(session: Session, library_id: str, item_ids: list[str]) -> dict[str, dict]:
    statuses = {
        item_id: {
            "pdf_status": "none",
            "pdf_count": 0,
            "parse_status": "none",
            "embedding_status": "none",
            "metadata_embedding_status": "none",
            "fulltext_embedding_status": "none",
            "knowledge_state": "metadata_only",
            "next_action": "embed_metadata",
        }
        for item_id in item_ids
    }
    if not item_ids:
        return statuses

    attachment_states: dict[str, list[str]] = {item_id: [] for item_id in item_ids}
    rows = session.execute(
        select(
            Attachment.item_id,
            Attachment.status,
            Attachment.mime,
            Attachment.logical_name,
            Attachment.object_path,
        ).where(Attachment.item_id.in_(item_ids))
    )
    for item_id, status, mime, logical_name, object_path in rows:
        if _looks_like_pdf(str(mime or ""), str(logical_name or ""), str(object_path or "")):
            attachment_states[item_id].append(str(status or "pending"))

    parsed_ids = set(
        session.scalars(
            select(Attachment.item_id)
            .join(DocumentArtifact, DocumentArtifact.attachment_id == Attachment.id)
            .where(Attachment.item_id.in_(item_ids))
            .where(DocumentArtifact.status == "ready")
        )
    )
    fulltext_embedded_ids = set(
        session.scalars(
            select(DocumentChunk.item_id)
            .where(DocumentChunk.item_id.in_(item_ids))
            .where(DocumentChunk.index_status == "ready")
        )
    )
    metadata_embedded_ids = set(
        session.scalars(
            select(ItemEmbedding.item_id)
            .where(ItemEmbedding.item_id.in_(item_ids))
            .where(ItemEmbedding.index_status == "ready")
        )
    )

    latest_jobs: dict[tuple[str, str], str] = {}
    metadata_job_status = ""
    jobs = session.scalars(
        select(Job)
        .where(
            Job.job_type.in_(
                [
                    JobType.RESOLVE_FULLTEXT.value,
                    JobType.PARSE_DOCUMENT.value,
                    JobType.EMBED_DOCUMENT.value,
                    JobType.EMBED_METADATA.value,
                ]
            )
        )
        .order_by(Job.created_at.desc())
    )
    for job in jobs:
        if job.job_type == JobType.EMBED_METADATA.value:
            if not metadata_job_status and str(job.payload.get("library_id") or "") == library_id:
                metadata_job_status = job.status
            continue
        item_id = str(job.payload.get("item_id") or "")
        key = (item_id, job.job_type)
        if item_id in statuses and key not in latest_jobs:
            latest_jobs[key] = job.status

    def job_stage(item_id: str, job_type: str, prerequisite: bool) -> str:
        value = latest_jobs.get((item_id, job_type), "")
        if value in {JobStatus.RUNNING.value, JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value}:
            return value
        if value in {JobStatus.FAILED.value, JobStatus.REVIEW_REQUIRED.value}:
            return value
        return "pending" if prerequisite else "none"

    for item_id, state in statuses.items():
        attachment_values = attachment_states[item_id]
        state["pdf_count"] = len(attachment_values)
        if "stored" in attachment_values:
            state["pdf_status"] = "ready"
        elif "linked" in attachment_values or "pending" in attachment_values:
            state["pdf_status"] = "queued"
        elif "invalid" in attachment_values:
            state["pdf_status"] = "failed"
        elif "missing" in attachment_values:
            state["pdf_status"] = "missing"
        else:
            state["pdf_status"] = job_stage(
                item_id,
                JobType.RESOLVE_FULLTEXT.value,
                False,
            )

        if item_id in parsed_ids:
            state["parse_status"] = "ready"
        else:
            state["parse_status"] = job_stage(
                item_id,
                JobType.PARSE_DOCUMENT.value,
                state["pdf_status"] == "ready",
            )
        if item_id in metadata_embedded_ids:
            state["metadata_embedding_status"] = "ready"
        elif metadata_job_status in {
            JobStatus.RUNNING.value,
            JobStatus.QUEUED.value,
            JobStatus.RETRY_WAIT.value,
            JobStatus.FAILED.value,
            JobStatus.REVIEW_REQUIRED.value,
        }:
            state["metadata_embedding_status"] = metadata_job_status

        if item_id in fulltext_embedded_ids:
            state["fulltext_embedding_status"] = "ready"
        else:
            state["fulltext_embedding_status"] = job_stage(
                item_id,
                JobType.EMBED_DOCUMENT.value,
                state["parse_status"] == "ready",
            )
        if "ready" in {
            state["metadata_embedding_status"],
            state["fulltext_embedding_status"],
        }:
            state["embedding_status"] = "ready"
        elif state["fulltext_embedding_status"] != "none":
            state["embedding_status"] = state["fulltext_embedding_status"]
        else:
            state["embedding_status"] = state["metadata_embedding_status"]

        if state["fulltext_embedding_status"] == "ready":
            state["knowledge_state"] = "fulltext_indexed"
            state["next_action"] = "none"
        elif state["parse_status"] == "ready":
            state["knowledge_state"] = "parsed"
            state["next_action"] = "embed_fulltext"
        elif state["pdf_status"] == "ready":
            state["knowledge_state"] = "pdf_stored"
            state["next_action"] = "parse_pdf"
        elif state["metadata_embedding_status"] == "ready":
            state["knowledge_state"] = "metadata_indexed"
            state["next_action"] = "resolve_pdf"
    return statuses


def _item_identities(session: Session, item_ids: list[str]) -> dict[str, dict]:
    identities = {item_id: {"canonical_key": f"item:{item_id}", "identifiers": {}} for item_id in item_ids}
    if not item_ids:
        return identities
    rows = session.execute(
        select(
            Identifier.item_id,
            Identifier.scheme,
            Identifier.normalized_value,
            Identifier.is_primary,
        )
        .where(Identifier.item_id.in_(item_ids))
        .order_by(Identifier.is_primary.desc(), Identifier.scheme, Identifier.normalized_value)
    )
    for item_id, scheme, value, _is_primary in rows:
        identities[item_id]["identifiers"].setdefault(str(scheme), str(value))
    for identity in identities.values():
        for scheme in ("doi", "pmcid", "pmid", "arxiv"):
            if value := identity["identifiers"].get(scheme):
                identity["canonical_key"] = f"{scheme}:{value}"
                break
    return identities


def _item_summaries(session: Session, library_id: str, items: list[Item]) -> list[dict]:
    item_ids = [item.id for item in items]
    pipeline = _item_pipeline_statuses(session, library_id, item_ids)
    identities = _item_identities(session, item_ids)
    return [
        {
            "id": item.id,
            "type": item.item_type,
            "title": item.title,
            "year": item.year,
            "container_title": item.container_title,
            "status": item.status,
            "doi": identities[item.id]["identifiers"].get("doi", ""),
            **identities[item.id],
            **pipeline[item.id],
        }
        for item in items
    ]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.load()
    state = AppState(resolved_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        resolved_settings.ensure_directories()
        upgrade_schema(resolved_settings)
        ResearchRunStore(state.database).mark_stale_runs_paused()
        worker_task = None
        parent_task = None
        if resolved_settings.worker_enabled:
            worker_task = asyncio.create_task(background_worker_loop(), name="researchbrain-worker")
        if state.parent_pid:
            parent_task = asyncio.create_task(
                exit_when_parent_stops(state.parent_pid),
                name="researchbrain-parent-watch",
            )
        try:
            yield
        finally:
            state.shutting_down = True
            active_research_tasks = list(state.research_tasks.values())
            for task in active_research_tasks:
                task.cancel()
            if active_research_tasks:
                await asyncio.gather(*active_research_tasks, return_exceptions=True)
            if worker_task:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
            if parent_task:
                parent_task.cancel()
                try:
                    await parent_task
                except asyncio.CancelledError:
                    pass
            await asyncio.to_thread(state.harness.stop)
            state.database.engine.dispose()

    app = FastAPI(title="ResearchBrain", version=__version__, lifespan=lifespan)
    app.state.researchbrain = state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "http://localhost:1420",
            "http://127.0.0.1:1420",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-ResearchBrain-Token"],
    )

    @app.middleware("http")
    async def local_session_auth(request: Request, call_next):
        if state.session_token and request.method != "OPTIONS":
            bearer = request.headers.get("Authorization", "")
            explicit = request.headers.get("X-ResearchBrain-Token", "")
            if bearer != f"Bearer {state.session_token}" and explicit != state.session_token:
                return JSONResponse(status_code=401, content={"detail": "invalid local session token"})
        return await call_next(request)

    def embedding_pipeline() -> EmbeddingPipeline:
        embedder = MiniMaxEmbedder(
            SecretStore().get("minimax_api_key"),
            resolved_settings.minimax_embedding_url,
            state.minimax_group_id,
            resolved_settings.minimax_embedding_model,
            resolved_settings.minimax_embedding_dimensions,
        )
        index = LanceIndex(
            resolved_settings.data_dir / "data" / "lancedb",
            embedder.model,
            embedder.dimensions,
        )
        return EmbeddingPipeline(state.database, resolved_settings.data_dir, embedder, index)

    def job_worker() -> JobWorker:
        return JobWorker(
            state.database,
            CrossrefProvider(
                resolved_settings.crossref_base_url,
                state.contact_email,
            ),
            ZoteroLocalClient(),
            FullTextPipeline(
                state.database,
                MultiSourceFullTextProvider(
                    [
                        UnpaywallProvider(
                            resolved_settings.unpaywall_base_url,
                            state.contact_email,
                        ),
                        OpenAlexFullTextProvider(
                            state.contact_email,
                            SecretStore().get("openalex_api_key"),
                        ),
                        PmcFullTextProvider(
                            state.contact_email,
                            SecretStore().get("ncbi_api_key"),
                        ),
                    ]
                ),
                ObjectStore(resolved_settings.data_dir, resolved_settings.max_download_mb),
            ),
            DocumentPipeline(
                state.database,
                resolved_settings.data_dir,
                FallbackParser(
                    MinerUParser(
                        state.mineru_executable,
                        resolved_settings.mineru_backend,
                        resolved_settings.mineru_version,
                    ),
                    PyMuPDFParser(),
                ),
            ),
            embedding_pipeline(),
            ZoteroAttachmentImporter(
                state.database,
                resolved_settings.data_dir,
                state.zotero_data_dir,
                resolved_settings.max_download_mb,
            ),
        )

    async def background_worker_loop() -> None:
        worker = job_worker()
        while True:
            try:
                job = await worker.run_one()
                await asyncio.sleep(0.05 if job else resolved_settings.worker_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background worker iteration failed")
                await asyncio.sleep(resolved_settings.worker_poll_seconds)

    def literature_discovery() -> LiteratureDiscovery:
        return LiteratureDiscovery(
            [
                CrossrefSearchProvider(resolved_settings.crossref_base_url, state.contact_email),
                OpenAlexSearchProvider(
                    state.contact_email,
                    SecretStore().get("openalex_api_key"),
                ),
                ArxivSearchProvider(),
                PubMedSearchProvider(
                    state.contact_email,
                    SecretStore().get("ncbi_api_key"),
                ),
            ]
        )

    def research_agent() -> ResearchAgent:
        generator = DeepSeekClient(
            SecretStore().get("deepseek_api_key"),
            resolved_settings.deepseek_base_url,
            resolved_settings.deepseek_model,
        )
        return ResearchAgent(embedding_pipeline(), generator, literature_discovery())

    run_store = ResearchRunStore(state.database)

    async def append_research_event(run_id: str, event_type: str, payload: dict) -> dict:
        lock = state.research_event_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            return await asyncio.to_thread(run_store.append_event, run_id, event_type, payload)

    async def execute_research_run(run_id: str) -> None:
        run = run_store.get_model(run_id)
        if not run:
            return
        signal = state.research_signals.setdefault(run_id, CancellationSignal())

        async def event_sink(event_type: str, payload: dict) -> None:
            await append_research_event(run_id, event_type, payload)

        async def steering_source() -> list[dict[str, str]]:
            return state.research_steering.pop(run_id, [])

        async def acquisition_source() -> dict | None:
            current = await asyncio.to_thread(run_store.get_model, run_id)
            if not current:
                return None
            approval = next(
                (value for value in reversed(current.approvals) if value.get("action") == "import_dois"),
                None,
            )
            if not approval:
                return None
            if approval.get("status") == "rejected":
                return {"decision": "rejected", "ready": False}
            if approval.get("status") != "approved":
                return {"decision": "pending", "ready": False}
            batch_id = str(approval.get("batch_id") or "")
            library_id = _run_library_id(state.database, current.session_id)
            with state.database.session() as session:
                batch = session.get(ImportBatch, batch_id) if batch_id else None
                batch_jobs = (
                    list(session.scalars(select(Job).where(Job.batch_id == batch_id))) if batch_id else []
                )
                item_ids = {
                    str(job.result.get("item_id") or "") for job in batch_jobs if job.result.get("item_id")
                }
                related_jobs = [
                    job
                    for job in session.scalars(select(Job))
                    if (
                        str(job.payload.get("item_id") or "") in item_ids
                        or (
                            job.job_type == JobType.EMBED_METADATA.value
                            and str(job.payload.get("library_id") or "") == library_id
                        )
                    )
                ]
            active_statuses = {
                JobStatus.QUEUED.value,
                JobStatus.RUNNING.value,
                JobStatus.RETRY_WAIT.value,
            }
            active = [job for job in related_jobs if job.status in active_statuses]
            batch_finished = bool(batch and batch.status in {"complete", "partial", "failed"})
            return {
                "decision": "approved",
                "batch_id": batch_id,
                "batch_status": batch.status if batch else "missing",
                "ready": batch_finished and not active,
                "active_jobs": len(active),
                "item_count": len(item_ids),
            }

        with state.database.session() as session:
            previous_messages = list(
                session.scalars(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == run.session_id,
                        ChatMessage.id != run.user_message_id,
                    )
                    .order_by(ChatMessage.created_at)
                )
            )
        history = [
            ConversationTurn(role=message.role, content=message.content)
            for message in previous_messages[-8:]
            if message.role in {"user", "assistant"}
        ]
        gateway = DeepSeekGateway(
            DeepSeekClient(
                SecretStore().get("deepseek_api_key"),
                resolved_settings.deepseek_base_url,
                resolved_settings.deepseek_model,
            )
        )
        budgets = ResearchBudgets.model_validate(run.budgets or {})
        orchestrator = ResearchOrchestrator(
            embedding_pipeline(),
            gateway,
            literature_discovery(),
            budgets=budgets,
            event_sink=event_sink,
            signal=signal,
            steering_source=steering_source,
            acquisition_source=acquisition_source,
        )
        try:
            answer = await orchestrator.run(
                _run_library_id(state.database, run.session_id),
                run.question,
                mode=run.mode,
                conversation_history=history,
                evidence_limit=budgets.evidence_limit,
                session_memory=run_store.load_memory(run.session_id),
            )
            message = await asyncio.to_thread(run_store.complete, run_id, answer)
            await event_sink(
                "run_completed",
                {
                    "message_id": message.id,
                    "metrics": answer.metrics or {},
                    "limitations": answer.limitations,
                },
            )
        except asyncio.CancelledError:
            if state.shutting_down:
                await asyncio.to_thread(
                    run_store.pause,
                    run_id,
                    "The application stopped before this research run completed",
                )
            else:
                await asyncio.to_thread(run_store.cancel, run_id)
                await event_sink("run_cancelled", {"message": "研究任务已停止"})
        except GenerationError as exc:
            if exc.code == "no_evidence":
                content = (
                    "当前文库没有可用于回答该问题的题录、摘要或已解析全文。"
                    "请先导入文献，或切换到“本地优先 + 联网”后重试。"
                    if run.mode == "local"
                    else "本次没有从当前文库或已启用的在线学术来源检索到可核验证据。"
                )
                answer = AgentAnswer(
                    answer=content,
                    evidence=[],
                    citation_ids=[],
                    limitations=["没有检索到可用于形成研究结论的证据。"],
                    model="local-readiness-check",
                    plan={},
                    coverage=[],
                    metrics={"empty_evidence": True},
                )
                message = await asyncio.to_thread(run_store.complete, run_id, answer)
                await event_sink(
                    "run_completed",
                    {"message_id": message.id, "metrics": answer.metrics, "limitations": answer.limitations},
                )
            else:
                await asyncio.to_thread(run_store.fail, run_id, exc.code, str(exc))
                await event_sink("run_failed", {"code": exc.code, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - persisted for diagnostics and surfaced to the client
            logger.exception("Research run %s failed", run_id)
            code = getattr(exc, "code", "research_run_failed")
            await asyncio.to_thread(run_store.fail, run_id, str(code), str(exc))
            await event_sink("run_failed", {"code": str(code), "message": str(exc)})
        finally:
            state.research_tasks.pop(run_id, None)
            state.research_signals.pop(run_id, None)

    def schedule_research_run(run_id: str) -> None:
        if run_id in state.research_tasks:
            raise ValueError("research run is already active")
        state.research_signals[run_id] = CancellationSignal()
        state.research_tasks[run_id] = asyncio.create_task(
            execute_research_run(run_id), name=f"research-run-{run_id}"
        )

    def get_session():
        with state.database.session() as session:
            yield session

    SessionDependency = Annotated[Session, Depends(get_session)]

    @app.get("/v1/health")
    def health(session: SessionDependency) -> dict:
        session.execute(select(func.count()).select_from(Library)).scalar_one()
        secrets = SecretStore().status()
        mineru_available = bool(
            shutil.which(state.mineru_executable) or Path(state.mineru_executable).expanduser().is_file()
        )
        return {
            "status": "ok",
            "version": __version__,
            "database": "ok",
            "data_dir": str(resolved_settings.data_dir),
            "components": {
                "mineru": "available" if mineru_available else "pymupdf_fallback",
                "lancedb": "available",
                "minimax": "configured" if secrets["minimax_api_key"] else "missing_key",
                "deepseek": "configured" if secrets["deepseek_api_key"] else "missing_key",
            },
        }

    @app.get("/v1/config/status")
    def config_status() -> dict:
        public_settings = asdict(resolved_settings)
        public_settings["data_dir"] = str(resolved_settings.data_dir)
        public_settings["database_url"] = "configured"
        public_settings["contact_email"] = state.contact_email
        public_settings["minimax_group_id"] = state.minimax_group_id
        public_settings["zotero_data_dir"] = str(state.zotero_data_dir)
        public_settings["mineru_executable"] = state.mineru_executable
        public_settings["harness_port"] = state.harness_port
        public_settings["secrets"] = SecretStore().status()
        return public_settings

    @app.put("/v1/config")
    def update_config(request: PublicConfigUpdateRequest, session: SessionDependency) -> dict:
        values = {key: value.strip() for key, value in request.model_dump(exclude_none=True).items()}
        state.user_config.update(values)
        retried_fulltext = 0
        if "contact_email" in values:
            state.contact_email = values["contact_email"]
            if state.contact_email:
                retried_fulltext = JobService(session).retry_failed_jobs(
                    job_types=[JobType.RESOLVE_FULLTEXT.value],
                    error_codes=["contact_email_missing"],
                )
        if "minimax_group_id" in values:
            state.minimax_group_id = values["minimax_group_id"]
        if "zotero_data_dir" in values:
            state.zotero_data_dir = Path(values["zotero_data_dir"]).expanduser()
        if "mineru_executable" in values:
            state.mineru_executable = values["mineru_executable"] or "mineru"
        return {
            "contact_email": state.contact_email,
            "minimax_group_id": state.minimax_group_id,
            "zotero_data_dir": str(state.zotero_data_dir),
            "mineru_executable": state.mineru_executable,
            "retried_fulltext": retried_fulltext,
        }

    @app.put("/v1/config/credential")
    def update_credential(request: CredentialUpdateRequest, session: SessionDependency) -> dict:
        try:
            SecretStore().set(request.name, request.value.strip())
        except SecretStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        retried = 0
        if request.name == "minimax_api_key" and request.value.strip():
            retried = JobService(session).retry_failed_jobs(
                job_types=[JobType.EMBED_DOCUMENT.value],
                error_codes=["api_key_missing", "authentication_failed"],
            )
        return {
            "name": request.name,
            "configured": bool(request.value.strip()),
            "retried": retried,
        }

    @app.post("/v1/libraries", response_model=LibraryResponse, status_code=201)
    def create_library(request: LibraryCreateRequest, session: SessionDependency) -> LibraryResponse:
        library = LibraryRepository(session).create_library(request.name, request.mode)
        return LibraryResponse.model_validate(library, from_attributes=True)

    @app.get("/v1/libraries", response_model=list[LibraryResponse])
    def list_libraries(session: SessionDependency) -> list[LibraryResponse]:
        return [
            LibraryResponse.model_validate(library, from_attributes=True)
            for library in LibraryRepository(session).list_libraries()
        ]

    @app.post("/v1/imports/doi", response_model=BatchResponse, status_code=202)
    def import_dois(request: DoiImportRequest, session: SessionDependency) -> BatchResponse:
        if not session.get(Library, request.library_id):
            raise HTTPException(status_code=404, detail="library not found")
        batch = JobService(session).create_doi_batch(
            request.library_id,
            request.dois,
            request.include_si,
            request.collection_id,
        )
        return BatchResponse.model_validate(batch, from_attributes=True)

    @app.get("/v1/imports/{batch_id}", response_model=BatchResponse)
    def get_batch(batch_id: str, session: SessionDependency) -> BatchResponse:
        batch = session.get(ImportBatch, batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="batch not found")
        return BatchResponse.model_validate(batch, from_attributes=True)

    @app.get("/v1/jobs")
    def list_jobs(session: SessionDependency, limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        jobs = JobService(session).list_jobs(limit)
        return [
            {
                "id": job.id,
                "batch_id": job.batch_id,
                "job_type": job.job_type,
                "status": job.status,
                "progress": job.progress,
                "attempt": job.attempt,
                "payload": job.payload,
                "result": job.result,
                "error_code": job.error_code,
                "error_message": job.error_message,
                "created_at": job.created_at,
                "finished_at": job.finished_at,
            }
            for job in jobs
        ]

    @app.post("/v1/jobs/run-next")
    async def run_next_job() -> dict:
        job = await job_worker().run_one()
        if not job:
            return {"status": "idle"}
        return {
            "id": job.id,
            "status": job.status,
            "job_type": job.job_type,
            "result": job.result,
            "error_code": job.error_code,
        }

    @app.post("/v1/jobs/{job_id}/retry")
    def retry_job(job_id: str, session: SessionDependency) -> dict:
        try:
            job = JobService(session).retry_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {"id": job.id, "status": job.status}

    @app.post("/v1/jobs/retry-failed")
    def retry_failed_jobs(request: BulkRetryRequest, session: SessionDependency) -> dict:
        count = JobService(session).retry_failed_jobs(request.library_id, request.job_types)
        return {"retried": count}

    @app.get("/v1/zotero/status")
    async def zotero_status() -> dict:
        try:
            return await ZoteroLocalClient().probe()
        except ZoteroConnectionError as exc:
            return {"available": False, "error": str(exc)}

    @app.post("/v1/libraries/{library_id}/zotero/sync", status_code=202)
    def queue_zotero_sync(library_id: str, session: SessionDependency) -> dict:
        library = session.get(Library, library_id)
        if not library:
            raise HTTPException(status_code=404, detail="library not found")
        if library.mode != "zotero_mirror":
            raise HTTPException(status_code=409, detail="library is not a Zotero mirror")
        job = JobService(session).create_zotero_sync_job(library_id)
        return {"id": job.id, "status": job.status, "job_type": job.job_type}

    @app.get("/v1/libraries/{library_id}/zotero/sync-status")
    def zotero_sync_status(library_id: str, session: SessionDependency) -> dict:
        library = session.get(Library, library_id)
        if not library:
            raise HTTPException(status_code=404, detail="library not found")
        if library.mode != "zotero_mirror":
            raise HTTPException(status_code=409, detail="library is not a Zotero mirror")
        items = list(
            session.scalars(
                select(Item).where(Item.library_id == library_id).where(Item.status != "tombstone")
            )
        )
        pipeline = _item_pipeline_statuses(session, library_id, [item.id for item in items])
        sync_jobs = list(
            session.scalars(
                select(Job).where(Job.job_type == JobType.ZOTERO_SYNC.value).order_by(Job.created_at.desc())
            )
        )
        latest = next(
            (job for job in sync_jobs if str(job.payload.get("library_id") or "") == library_id),
            None,
        )
        return {
            "library_id": library.id,
            "library_name": library.name,
            "last_version": library.last_version or 0,
            "counts": {
                "items": len(items),
                "pdf_ready": sum(value["pdf_status"] == "ready" for value in pipeline.values()),
                "parsed": sum(value["parse_status"] == "ready" for value in pipeline.values()),
                "embedded": sum(value["embedding_status"] == "ready" for value in pipeline.values()),
            },
            "job": (
                {
                    "id": latest.id,
                    "status": latest.status,
                    "progress": latest.progress,
                    "result": latest.result,
                    "error_code": latest.error_code,
                    "error_message": latest.error_message,
                    "created_at": latest.created_at,
                    "finished_at": latest.finished_at,
                }
                if latest
                else None
            ),
        }

    @app.post("/v1/search")
    async def search(request: SearchRequest, session: SessionDependency) -> list[dict]:
        if not session.get(Library, request.library_id):
            raise HTTPException(status_code=404, detail="library not found")
        try:
            hits = await embedding_pipeline().search(
                request.query,
                request.library_id,
                max(1, min(request.limit, 50)),
            )
        except EmbeddingError as exc:
            raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return [
            {
                "chunk_id": hit.chunk_id,
                "item_id": hit.item_id,
                "artifact_id": hit.artifact_id,
                "title": hit.title,
                "text": hit.text,
                "section": hit.section,
                "page_start": hit.page_start,
                "page_end": hit.page_end,
                "score": hit.score,
                "vector_rank": hit.vector_rank,
                "keyword_rank": hit.keyword_rank,
            }
            for hit in hits
        ]

    @app.post("/v1/chat/sessions", status_code=201)
    def create_chat_session(request: ChatSessionCreateRequest, session: SessionDependency) -> dict:
        if not session.get(Library, request.library_id):
            raise HTTPException(status_code=404, detail="library not found")
        chat_session = ChatSession(
            library_id=request.library_id,
            title=request.title.strip() or "New research",
        )
        session.add(chat_session)
        session.flush()
        return {
            "id": chat_session.id,
            "library_id": chat_session.library_id,
            "title": chat_session.title,
            "created_at": chat_session.created_at,
        }

    @app.get("/v1/chat/sessions")
    def list_chat_sessions(
        library_id: str,
        session: SessionDependency,
    ) -> list[dict]:
        if not session.get(Library, library_id):
            raise HTTPException(status_code=404, detail="library not found")
        message_count = (
            select(func.count(ChatMessage.id))
            .where(ChatMessage.session_id == ChatSession.id)
            .correlate(ChatSession)
            .scalar_subquery()
        )
        latest_content = (
            select(ChatMessage.content)
            .where(ChatMessage.session_id == ChatSession.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
            .correlate(ChatSession)
            .scalar_subquery()
        )
        rows = session.execute(
            select(
                ChatSession,
                message_count.label("message_count"),
                latest_content.label("latest_content"),
            )
            .where(ChatSession.library_id == library_id)
            .order_by(ChatSession.updated_at.desc())
        ).all()
        response = []
        for chat_session, count, latest in rows:
            response.append(
                {
                    "id": chat_session.id,
                    "library_id": chat_session.library_id,
                    "title": chat_session.title,
                    "message_count": int(count or 0),
                    "last_message_preview": (latest[:160] if latest else ""),
                    "created_at": chat_session.created_at,
                    "updated_at": chat_session.updated_at,
                }
            )
        return response

    @app.get("/v1/chat/sessions/{chat_session_id}/messages")
    def list_chat_messages(chat_session_id: str, session: SessionDependency) -> list[dict]:
        if not session.get(ChatSession, chat_session_id):
            raise HTTPException(status_code=404, detail="chat session not found")
        messages = list(
            session.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == chat_session_id)
                .order_by(ChatMessage.created_at)
            )
        )
        return [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "citations": message.citations,
                "model": message.model,
                "created_at": message.created_at,
            }
            for message in messages
        ]

    @app.post("/v1/chat/sessions/{chat_session_id}/runs", status_code=202)
    async def create_research_run(chat_session_id: str, request: ResearchRunRequest) -> dict:
        if not resolved_settings.research_loop_v2:
            raise HTTPException(status_code=409, detail="research loop v2 is disabled")
        question = request.content.strip()
        if not question:
            raise HTTPException(status_code=422, detail="message content is empty")
        with state.database.session() as session:
            chat_session = session.get(ChatSession, chat_session_id)
            if not chat_session:
                raise HTTPException(status_code=404, detail="chat session not found")
            active = session.scalar(
                select(ResearchRun.id).where(
                    ResearchRun.session_id == chat_session_id,
                    ResearchRun.status.in_(["queued", "running", "cancelling"]),
                )
            )
            if active:
                raise HTTPException(status_code=409, detail="this chat already has an active research run")
            message = ChatMessage(session_id=chat_session_id, role="user", content=question)
            session.add(message)
            session.flush()
            user_message_id = message.id
            if chat_session.title == "New research":
                chat_session.title = question[:100]
            chat_session.updated_at = datetime.now(UTC)
        budgets = request.budgets or ResearchBudgets(
            parallel_scouts=resolved_settings.research_parallel_scouts
        )
        budgets = budgets.model_copy(update={"evidence_limit": request.evidence_limit})
        run = await asyncio.to_thread(
            run_store.create,
            chat_session_id,
            user_message_id,
            question,
            request.mode,
            budgets.model_dump(),
        )
        await append_research_event(run.id, "run_created", {"status": "queued", "mode": request.mode})
        schedule_research_run(run.id)
        return run_store.get(run.id) or {"id": run.id, "status": "queued"}

    @app.get("/v1/research/runs/{run_id}")
    def get_research_run(run_id: str) -> dict:
        run = run_store.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="research run not found")
        return run

    @app.get("/v1/chat/sessions/{chat_session_id}/runs")
    def list_research_runs(chat_session_id: str, limit: int = 20) -> list[dict]:
        with state.database.session() as session:
            if not session.get(ChatSession, chat_session_id):
                raise HTTPException(status_code=404, detail="chat session not found")
        return run_store.list_for_session(chat_session_id, limit)

    @app.get("/v1/research/runs/{run_id}/events")
    async def stream_research_events(run_id: str, request: Request, after: int = 0):
        if not run_store.get(run_id):
            raise HTTPException(status_code=404, detail="research run not found")
        header_sequence = request.headers.get("Last-Event-ID", "")
        try:
            cursor = max(after, int(header_sequence or 0))
        except ValueError:
            cursor = after

        async def event_stream():
            nonlocal cursor
            idle_polls = 0
            while True:
                if await request.is_disconnected():
                    return
                events = await asyncio.to_thread(run_store.events_after, run_id, cursor)
                for event in events:
                    cursor = int(event["sequence"])
                    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {payload}\n\n"
                run = await asyncio.to_thread(run_store.get, run_id)
                if not run:
                    return
                if run["status"] in TERMINAL_RUN_STATUSES and not events:
                    if await asyncio.to_thread(run_store.has_terminal_event, run_id):
                        return
                if events:
                    idle_polls = 0
                else:
                    idle_polls += 1
                    if idle_polls >= 40:
                        idle_polls = 0
                        yield ": keepalive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/research/runs/{run_id}/cancel")
    async def cancel_research_run(run_id: str) -> dict:
        run = run_store.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="research run not found")
        if run["status"] in TERMINAL_RUN_STATUSES:
            return run
        signal = state.research_signals.get(run_id)
        if signal:
            signal.cancel()
        task = state.research_tasks.get(run_id)
        if task:
            task.cancel()
        else:
            await asyncio.to_thread(run_store.cancel, run_id)
            await append_research_event(run_id, "run_cancelled", {"message": "研究任务已停止"})
        return run_store.get(run_id) or run

    @app.post("/v1/research/runs/{run_id}/steer")
    async def steer_research_run(run_id: str, request: ResearchSteerRequest) -> dict:
        run = run_store.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="research run not found")
        if run["status"] in TERMINAL_RUN_STATUSES:
            raise HTTPException(status_code=409, detail="research run is no longer active")
        message = {"kind": request.kind, "content": request.content.strip()}
        state.research_steering.setdefault(run_id, []).append(message)
        await append_research_event(run_id, "steering_queued", message)
        return {"run_id": run_id, "queued": True, **message}

    @app.post("/v1/research/runs/{run_id}/retry", status_code=202)
    async def retry_research_run(run_id: str) -> dict:
        active_task = state.research_tasks.get(run_id)
        if active_task:
            persisted = run_store.get(run_id)
            if not persisted or persisted["status"] not in {"failed", "paused", "cancelled"}:
                raise HTTPException(status_code=409, detail="research run is already active")
            try:
                await asyncio.shield(active_task)
            except asyncio.CancelledError:
                pass
            state.research_tasks.pop(run_id, None)
        try:
            await asyncio.to_thread(run_store.reset_for_retry, run_id)
            await append_research_event(run_id, "run_retried", {})
            schedule_research_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return run_store.get(run_id) or {"id": run_id, "status": "queued"}

    @app.post("/v1/research/runs/{run_id}/approvals/{approval_id}", status_code=202)
    async def approve_research_action(run_id: str, approval_id: str) -> dict:
        run = run_store.get_model(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="research run not found")
        approval = next((value for value in run.approvals if value.get("id") == approval_id), None)
        if not approval:
            raise HTTPException(status_code=404, detail="approval not found")
        if approval.get("status") != "pending":
            raise HTTPException(status_code=409, detail="approval has already been handled")
        if approval.get("action") != "import_dois":
            raise HTTPException(status_code=422, detail="unsupported research approval action")
        dois = [str(value) for value in approval.get("dois") or []]
        library_id = _run_library_id(state.database, run.session_id)
        with state.database.session() as session:
            batch = JobService(session).create_doi_batch(library_id, dois, False)
            batch_id = batch.id
        approved = await asyncio.to_thread(run_store.approve, run_id, approval_id, batch_id)
        await append_research_event(
            run_id,
            "approval_completed",
            {"approval_id": approval_id, "batch_id": batch_id, "dois": dois},
        )
        return {"run_id": run_id, "batch_id": batch_id, "approval": approved}

    @app.post("/v1/research/runs/{run_id}/approvals/{approval_id}/reject")
    async def reject_research_action(run_id: str, approval_id: str) -> dict:
        try:
            rejected = await asyncio.to_thread(run_store.reject, run_id, approval_id)
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if "not found" in detail else 409
            raise HTTPException(status_code=status_code, detail=detail) from exc
        await append_research_event(run_id, "approval_rejected", {"approval_id": approval_id})
        return {"run_id": run_id, "approval": rejected}

    @app.post("/v1/chat/sessions/{chat_session_id}/messages")
    async def send_chat_message(chat_session_id: str, request: ChatMessageRequest) -> dict:
        question = request.content.strip()
        if not question:
            raise HTTPException(status_code=422, detail="message content is empty")
        with state.database.session() as session:
            chat_session = session.get(ChatSession, chat_session_id)
            if not chat_session:
                raise HTTPException(status_code=404, detail="chat session not found")
            library_id = chat_session.library_id
            previous_messages = list(
                session.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == chat_session_id)
                    .order_by(ChatMessage.created_at)
                )
            )
            conversation_history = [
                ConversationTurn(role=message.role, content=message.content)
                for message in previous_messages[-6:]
                if message.role in {"user", "assistant"}
            ]
            session.add(ChatMessage(session_id=chat_session_id, role="user", content=question))
            if chat_session.title == "New research":
                chat_session.title = question[:100]
            chat_session.updated_at = datetime.now(UTC)
        try:
            answer = await research_agent().answer(
                library_id,
                question,
                max(1, min(request.evidence_limit, 20)),
                request.mode,
                conversation_history,
            )
        except GenerationError as exc:
            if exc.code == "no_evidence":
                if request.mode == "local":
                    content = (
                        "当前文库没有可用于回答该问题的题录、摘要或已解析全文。"
                        "请先导入文献，或切换到“本地优先 + 联网”后重试。"
                    )
                    limitations = ["没有检索到本地证据，因此未生成研究结论。"]
                else:
                    content = (
                        "本次没有从已启用的在线学术来源检索到可核验记录。"
                        "请调整关键词、年份或来源后重试；也可以使用 Google Scholar 补充检索。"
                    )
                    limitations = ["在线来源没有返回可用于回答的题录或摘要。"]
                with state.database.session() as session:
                    message = ChatMessage(
                        session_id=chat_session_id,
                        role="assistant",
                        content=content,
                        citations=[],
                        model="local-readiness-check",
                    )
                    session.add(message)
                    session.flush()
                    return {
                        "id": message.id,
                        "role": message.role,
                        "content": message.content,
                        "citations": [],
                        "limitations": limitations,
                        "model": message.model,
                    }
            raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
        except EmbeddingError as exc:
            raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "retrieval_error", "message": str(exc)},
            ) from exc
        citations = [asdict(value) for value in answer.evidence]
        with state.database.session() as session:
            message = ChatMessage(
                session_id=chat_session_id,
                role="assistant",
                content=answer.answer,
                citations=citations,
                model=answer.model,
            )
            session.add(message)
            session.flush()
            return {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "citations": citations,
                "limitations": answer.limitations,
                "search_queries": answer.search_queries or [],
                "provider_statuses": [asdict(value) for value in (answer.provider_statuses or [])],
                "model": answer.model,
            }

    @app.post("/v1/exports")
    def export_references(request: ExportRequest, session: SessionDependency) -> dict:
        try:
            artifact = CitationExporter(session).export(request.item_ids, request.format)
        except CitationExportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "filename": artifact.filename,
            "mime": artifact.mime,
            "content": artifact.content,
        }

    @app.post("/v1/discovery/search")
    async def discover_literature(request: DiscoverySearchRequest) -> dict:
        query = request.query.strip()
        if not query:
            raise HTTPException(status_code=422, detail="search query is empty")
        providers = {
            "crossref": CrossrefSearchProvider(
                resolved_settings.crossref_base_url,
                state.contact_email,
            ),
            "openalex": OpenAlexSearchProvider(
                state.contact_email,
                SecretStore().get("openalex_api_key"),
            ),
            "arxiv": ArxivSearchProvider(),
            "pubmed": PubMedSearchProvider(
                state.contact_email,
                SecretStore().get("ncbi_api_key"),
            ),
        }
        selected = [providers[name] for name in dict.fromkeys(request.sources) if name in providers]
        if not selected:
            raise HTTPException(status_code=422, detail="at least one search source is required")
        result = await LiteratureDiscovery(selected).search_with_status(
            query,
            max(1, min(request.limit_per_source, 50)),
        )
        records = [
            record
            for record in result.records
            if (request.year_from is None or record.year is None or record.year >= request.year_from)
            and (request.year_to is None or record.year is None or record.year <= request.year_to)
            and (not request.oa_only or record.is_oa)
        ]
        return {
            "records": [asdict(record) for record in records],
            "providers": [asdict(value) for value in result.providers],
        }

    @app.post("/v1/discovery/import", status_code=202)
    def import_discovery_records(request: DiscoveryImportRequest) -> dict:
        with state.database.session() as session:
            if not session.get(Library, request.library_id):
                raise HTTPException(status_code=404, detail="library not found")
            repository = LibraryRepository(session)
            jobs = JobService(session)
            created = 0
            duplicates = 0
            fulltext_queued = 0
            item_ids: list[str] = []
            for value in request.records:
                identifiers = dict(value.identifiers)
                if value.doi:
                    identifiers["doi"] = value.doi
                if value.source_id and value.source in {"pubmed", "arxiv", "openalex"}:
                    scheme = {"pubmed": "pmid", "arxiv": "arxiv", "openalex": "openalex"}[value.source]
                    identifiers.setdefault(scheme, value.source_id.rsplit("/", 1)[-1])
                reference = ReferenceRecord(
                    type=value.publication_type,
                    title=value.title,
                    abstract=value.abstract,
                    year=value.year,
                    container_title=value.venue,
                    url=value.url,
                    identifiers=identifiers,
                    creators=[CreatorInput(literal=name) for name in value.authors if name.strip()],
                    raw={"discovery": value.model_dump()},
                )
                provider = "discovery:" + ",".join(value.sources or [value.source])
                item, was_created = repository.add_reference(
                    request.library_id,
                    reference,
                    provider,
                )
                item_ids.append(item.id)
                created += int(was_created)
                duplicates += int(not was_created)
                doi = reference.identifiers.get("doi", "")
                if doi or (value.is_oa and value.fulltext_url):
                    job = jobs.create_fulltext_job(
                        request.library_id,
                        item.id,
                        doi,
                        request.include_si,
                    )
                    fulltext_queued += int(job.status == JobStatus.QUEUED.value)
            embedding_job = jobs.create_metadata_embedding_job(request.library_id)
            return {
                "created": created,
                "duplicates": duplicates,
                "item_ids": list(dict.fromkeys(item_ids)),
                "fulltext_queued": fulltext_queued,
                "embedding_job_id": embedding_job.id,
            }

    @app.get("/v1/runtime/components")
    def runtime_components() -> dict:
        return RuntimeManager(resolved_settings.data_dir).status()

    @app.post("/v1/runtime/components/install")
    def install_runtime_component(request: RuntimeInstallRequest) -> dict:
        try:
            state = RuntimeManager(resolved_settings.data_dir).install_archive(
                request.name,
                request.version,
                Path(request.archive_path),
                request.sha256,
            )
        except (OSError, RuntimeInstallError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return asdict(state)

    @app.get("/v1/harness/status")
    def harness_status() -> dict:
        return state.harness.status(state.harness_port)

    @app.post("/v1/harness/install")
    async def install_harness(request: HarnessActionRequest) -> dict:
        if request.library_id:
            with state.database.session() as session:
                if not session.get(Library, request.library_id):
                    raise HTTPException(status_code=404, detail="library not found")
        state.harness_port = request.port
        state.user_config.update({"harness_port": request.port})
        try:
            return await asyncio.to_thread(state.harness.install, request.library_id)
        except (OSError, HarnessInstallError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/harness/start")
    async def start_harness(request: HarnessActionRequest) -> dict:
        if request.library_id:
            with state.database.session() as session:
                if not session.get(Library, request.library_id):
                    raise HTTPException(status_code=404, detail="library not found")
        state.harness_port = request.port
        state.user_config.update({"harness_port": request.port})
        try:
            return await asyncio.to_thread(
                state.harness.start,
                request.port,
                request.library_id,
                SecretStore().get("deepseek_api_key"),
                resolved_settings.deepseek_base_url,
                resolved_settings.deepseek_model,
            )
        except (OSError, HarnessInstallError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/harness/stop")
    async def stop_harness() -> dict:
        return await asyncio.to_thread(state.harness.stop)

    @app.get("/v1/skills")
    def list_skills() -> list[dict]:
        return state.harness.skills.list()

    @app.post("/v1/skills", status_code=201)
    async def install_skill(request: SkillInstallRequest) -> dict:
        try:
            return await asyncio.to_thread(
                state.harness.skills.install,
                request.source_kind,
                request.source,
                ref=request.ref,
                subpath=request.subpath,
                enabled=request.enabled,
            )
        except (OSError, SkillError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/skills/{name}/update")
    async def update_skill(name: str) -> dict:
        try:
            return await asyncio.to_thread(state.harness.skills.update, name)
        except (OSError, SkillError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/v1/skills/{name}/enabled")
    def enable_skill(name: str, request: SkillEnableRequest) -> dict:
        try:
            return state.harness.skills.set_enabled(name, request.enabled)
        except (OSError, SkillError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/v1/skills/{name}", status_code=204)
    def uninstall_skill(name: str) -> None:
        try:
            state.harness.skills.uninstall(name)
        except (OSError, SkillError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/skills/{name}/reveal")
    async def reveal_skill(name: str) -> dict:
        try:
            path = await asyncio.to_thread(state.harness.skills.reveal, name)
        except (OSError, SkillError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"path": path}

    @app.post("/v1/skills/{name}/launch")
    async def launch_skill(name: str, request: SkillLaunchRequest) -> dict:
        with state.database.session() as session:
            library = session.get(Library, request.library_id)
            if not library:
                raise HTTPException(status_code=404, detail="library not found")
            library_name = library.name
        try:
            prompt = state.harness.skills.launch_prompt(name, library_name)
            current = state.harness.status(request.port)
            if not current["configured"]:
                raise SkillError("Install the Harness environment before using a Skill")
            if current["running"] and current["skills"]["restart_required"]:
                if not current["owned_process"]:
                    raise SkillError(
                        "Harness is running outside ResearchBrain; stop it before deploying changed Skills"
                    )
                await asyncio.to_thread(state.harness.stop)
            status = await asyncio.to_thread(
                state.harness.start,
                request.port,
                request.library_id,
                SecretStore().get("deepseek_api_key"),
                resolved_settings.deepseek_base_url,
                resolved_settings.deepseek_model,
            )
        except (OSError, HarnessInstallError, SkillError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"skill": name, "prompt": prompt, "harness": status}

    @app.get("/v1/libraries/{library_id}/items")
    def list_items(
        library_id: str,
        session: SessionDependency,
        limit: int = Query(100, ge=1, le=1000),
    ) -> list[dict]:
        if not session.get(Library, library_id):
            raise HTTPException(status_code=404, detail="library not found")
        items = list(
            session.scalars(
                select(Item)
                .where(Item.library_id == library_id)
                .order_by(Item.created_at.desc())
                .limit(limit)
            )
        )
        return _item_summaries(session, library_id, items)

    @app.post("/v1/libraries/{library_id}/items/lookup")
    def lookup_literature(
        library_id: str,
        request: LiteratureLookupRequest,
        session: SessionDependency,
    ) -> dict:
        if not session.get(Library, library_id):
            raise HTTPException(status_code=404, detail="library not found")
        try:
            doi = normalize_doi(request.doi)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        items = list(
            session.scalars(
                select(Item)
                .join(Identifier, Identifier.item_id == Item.id)
                .where(Item.library_id == library_id)
                .where(Item.status != "tombstone")
                .where(Identifier.scheme == "doi")
                .where(Identifier.normalized_value == doi)
                .order_by(Item.created_at.desc())
            )
        )
        summaries = _item_summaries(session, library_id, items)
        pdf_sha256 = request.pdf_sha256.lower() if request.pdf_sha256 else None
        exact_pdf_item_ids: set[str] = set()
        if pdf_sha256 and items:
            exact_pdf_item_ids.update(
                session.scalars(
                    select(Attachment.item_id)
                    .where(Attachment.item_id.in_([item.id for item in items]))
                    .where(Attachment.sha256 == pdf_sha256)
                    .where(Attachment.status == "stored")
                )
            )

        state_priority = {
            "metadata_only": 0,
            "metadata_indexed": 1,
            "pdf_stored": 2,
            "parsed": 3,
            "fulltext_indexed": 4,
        }
        candidates = summaries
        if exact_pdf_item_ids:
            candidates = [summary for summary in summaries if summary["id"] in exact_pdf_item_ids]
        best = (
            max(candidates, key=lambda value: state_priority[value["knowledge_state"]])
            if candidates
            else None
        )
        if not summaries:
            recommended_action = "import_metadata_and_pdf"
        elif pdf_sha256 and not exact_pdf_item_ids:
            recommended_action = "attach_pdf"
        else:
            recommended_action = best["next_action"] if best else "embed_metadata"
        return {
            "found": bool(summaries),
            "canonical_key": f"doi:{doi}",
            "doi": doi,
            "pdf_sha256": pdf_sha256,
            "exact_pdf_known": bool(exact_pdf_item_ids) if pdf_sha256 else None,
            "recommended_action": recommended_action,
            "matches": summaries,
        }

    @app.post("/v1/items/{item_id}/attachments", status_code=201)
    async def upload_attachment(item_id: str, file: Annotated[UploadFile, File()]) -> dict:
        with state.database.session() as session:
            item = session.get(Item, item_id)
            if not item:
                raise HTTPException(status_code=404, detail="item not found")
            library_id = item.library_id

        async def chunks():
            while chunk := await file.read(1024 * 128):
                yield chunk

        try:
            stored = await ObjectStore(
                resolved_settings.data_dir,
                resolved_settings.max_download_mb,
            ).store_pdf_stream(chunks())
        except DownloadError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
        finally:
            await file.close()

        with state.database.session() as session:
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
                    logical_name=file.filename or "Full Text.pdf",
                    object_path=str(stored.path.relative_to(resolved_settings.data_dir)),
                    mime=stored.mime,
                    source_url="manual-upload",
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
                "id": attachment.id,
                "sha256": attachment.sha256,
                "bytes": attachment.bytes,
                "reused": reused,
                "parse_job_id": job.id,
            }

    @app.post("/v1/items/{item_id}/fulltext", status_code=202)
    def queue_item_fulltext(item_id: str, session: SessionDependency) -> dict:
        item = session.get(Item, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="item not found")
        stored_pdf = session.scalar(
            select(Attachment.id)
            .where(Attachment.item_id == item_id)
            .where(Attachment.status == "stored")
            .where(
                (func.lower(Attachment.mime) == "application/pdf")
                | func.lower(Attachment.logical_name).like("%.pdf")
                | func.lower(Attachment.object_path).like("%.pdf")
            )
            .limit(1)
        )
        if stored_pdf:
            raise HTTPException(
                status_code=409,
                detail={"code": "pdf_already_available", "message": "PDF is already stored"},
            )
        doi = session.scalar(
            select(Identifier.normalized_value)
            .where(Identifier.item_id == item_id)
            .where(Identifier.scheme == "doi")
            .order_by(Identifier.is_primary.desc())
            .limit(1)
        )
        if not doi:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "doi_missing",
                    "message": "该文献没有 DOI，无法自动查找 PDF；请选择本地 PDF 文件",
                },
            )
        job, requeued = JobService(session).queue_fulltext_job(
            item.library_id,
            item.id,
            normalize_doi(doi),
            include_si=False,
        )
        return {
            "id": job.id,
            "status": job.status,
            "job_type": job.job_type,
            "doi": normalize_doi(doi),
            "requeued": requeued,
        }

    @app.get("/v1/items/{item_id}/attachments")
    def list_item_attachments(item_id: str, session: SessionDependency) -> list[dict]:
        if not session.get(Item, item_id):
            raise HTTPException(status_code=404, detail="item not found")
        attachments = list(
            session.scalars(
                select(Attachment).where(Attachment.item_id == item_id).order_by(Attachment.created_at.desc())
            )
        )
        return [
            {
                "id": attachment.id,
                "logical_name": attachment.logical_name,
                "mime": attachment.mime,
                "status": attachment.status,
                "bytes": attachment.bytes,
                "sha256": attachment.sha256,
                "source_url": attachment.source_url,
            }
            for attachment in attachments
            if _looks_like_pdf(
                attachment.mime,
                attachment.logical_name,
                attachment.object_path,
            )
        ]

    @app.get("/v1/attachments/{attachment_id}/content")
    def attachment_content(attachment_id: str, session: SessionDependency) -> FileResponse:
        attachment = session.get(Attachment, attachment_id)
        if not attachment or attachment.status != "stored":
            raise HTTPException(status_code=404, detail="stored PDF not found")
        path = _safe_data_path(resolved_settings.data_dir, attachment.object_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="stored PDF file is missing")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=attachment.logical_name or "document.pdf",
            content_disposition_type="inline",
        )

    @app.get("/v1/items/{item_id}/artifacts")
    def list_item_artifacts(item_id: str, session: SessionDependency) -> list[dict]:
        if not session.get(Item, item_id):
            raise HTTPException(status_code=404, detail="item not found")
        artifacts = list(
            session.scalars(
                select(DocumentArtifact)
                .join(Attachment, Attachment.id == DocumentArtifact.attachment_id)
                .where(Attachment.item_id == item_id)
                .where(DocumentArtifact.status == "ready")
                .order_by(DocumentArtifact.created_at.desc())
            )
        )
        return [
            {
                "id": artifact.id,
                "parser_name": artifact.parser_name,
                "page_count": artifact.page_count,
                "created_at": artifact.created_at,
            }
            for artifact in artifacts
        ]

    @app.get("/v1/artifacts/{artifact_id}/markdown")
    def artifact_markdown(artifact_id: str, session: SessionDependency) -> FileResponse:
        artifact = session.get(DocumentArtifact, artifact_id)
        if not artifact or artifact.status != "ready":
            raise HTTPException(status_code=404, detail="parsed document not found")
        path = _safe_data_path(resolved_settings.data_dir, artifact.markdown_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="parsed Markdown file is missing")
        return FileResponse(path, media_type="text/markdown; charset=utf-8")

    return app


app = create_app()
