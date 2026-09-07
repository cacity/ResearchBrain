import pytest

from researchbrain.agent.gateway import CancellationSignal
from researchbrain.agent.service import Evidence
from researchbrain.db.base import Database
from researchbrain.db.migrations import upgrade_schema
from researchbrain.db.models import Attachment, DocumentArtifact, DocumentChunk, Job
from researchbrain.domain import CreatorInput, JobStatus, LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository
from researchbrain.orchestration.acquisition import ResearchAcquisitionTools
from researchbrain.orchestration.models import CoverageItem
from researchbrain.orchestration.orchestrator import (
    ResearchOrchestrator,
    _plan_acquisition_actions,
    _tool_approval_key,
)
from researchbrain.orchestration.tools import ImportDoisArguments
from tests.test_orchestrator import FixtureGateway, FixtureRetrieval


class MetadataProvider:
    name = "fixture"

    async def resolve_doi(self, doi):
        return ReferenceRecord(
            title="Resolved paper",
            abstract="An abstract",
            identifiers={"doi": doi},
            creators=[CreatorInput(family="Author")],
            year=2024,
        )


@pytest.mark.research_doi_pdf_loop
def test_agent_acquisition_decision_uses_coverage_gaps_and_fulltext_requirements():
    online = Evidence(
        id="L1",
        item_id="online-1",
        chunk_id="online-1",
        title="Online paper",
        text="Abstract-only online record",
        score=0.9,
        source_kind="online",
        source_name="openalex",
        section="",
        page_start=None,
        page_end=None,
        discovery_record={"doi": "10.1000/Gap"},
    )
    covered = [
        CoverageItem(
            subquestion_id="Q1",
            question="landscape",
            status="covered",
            required_level="structured_abstract",
            evidence_ids=["L1"],
        )
    ]
    assert _plan_acquisition_actions(covered, [online]).dois == []

    gap = [
        CoverageItem(
            subquestion_id="Q1",
            question="method details",
            status="partial",
            required_level="fulltext_page",
            evidence_ids=["L1"],
            missing=["需要全文方法细节"],
        )
    ]
    decision = _plan_acquisition_actions(gap, [online])
    assert decision.dois == ["10.1000/gap"]
    assert decision.wait_for_parse is True
    assert decision.actions == [
        "lookup_doi",
        "import_dois",
        "queue_fulltext",
        "job_status",
        "parse_pdf",
        "embed_document",
        "wait_for_parse",
    ]
    assert decision.evidence_ids == ["L1"]


@pytest.mark.asyncio
@pytest.mark.research_doi_pdf_loop
async def test_doi_pdf_tools_are_registered_scoped_approved_idempotent_and_chain_jobs(settings):
    settings.ensure_directories()
    upgrade_schema(settings)
    database = Database(settings.database_url)
    with database.session() as session:
        library = LibraryRepository(session).create_library("DOI", LibraryMode.STANDALONE)
        repository = LibraryRepository(session)
        item, _ = repository.add_reference(
            library.id,
            ReferenceRecord(title="Paper", identifiers={"doi": "10.1000/example"}),
            "fixture",
        )
        queue_item, _ = repository.add_reference(
            library.id,
            ReferenceRecord(title="Paper without PDF", identifiers={"doi": "10.1000/queue"}),
            "fixture",
        )
        attachment = Attachment(
            item_id=item.id,
            sha256="a" * 64,
            logical_name="paper.pdf",
            object_path="objects/paper.pdf",
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
            markdown_path="documents/paper.md",
            document_json_path="documents/paper.json",
            content_hash="b" * 64,
            page_count=3,
            status="ready",
        )
        session.add(artifact)
        session.flush()
        library_id = library.id
        item_id = item.id
        queue_item_id = queue_item.id
        attachment_id = attachment.id
        artifact_id = artifact.id

    events = []

    async def sink(kind, payload):
        events.append((kind, payload))

    handlers = ResearchAcquisitionTools(database, MetadataProvider())
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[]]),
        FixtureGateway(),
        acquisition_tools=handlers,
        event_sink=sink,
        signal=CancellationSignal(),
    )
    orchestrator.current_library_id = library_id
    definitions = {value["name"]: value for value in orchestrator.tools.definitions}
    assert {
        "lookup_doi",
        "import_dois",
        "queue_fulltext",
        "job_status",
        "parse_pdf",
        "embed_document",
        "export_references",
    } <= definitions.keys()
    assert definitions["import_dois"]["approval_required"] is True
    assert definitions["import_dois"]["idempotency"] == "required_key"
    assert definitions["job_status"]["readonly"] is True

    lookup = await orchestrator.tools.execute_many("lookup_doi", [{"doi": "https://doi.org/10.1000/X"}])
    assert lookup[0].value["doi"] == "10.1000/x"

    import_payload = {
        "library_id": library_id,
        "dois": ["10.1000/X", "https://doi.org/10.1000/x"],
        "approval_id": "approval-1",
        "idempotency_key": "import:approval-1",
    }
    denied = await orchestrator.tools.execute_many("import_dois", [import_payload], parallel=False)
    assert denied[0].error_code == "approval_required"
    parsed = ImportDoisArguments.model_validate(import_payload)
    orchestrator.approved_tool_call_keys.add(_tool_approval_key("import_dois", parsed))
    imported = await orchestrator.tools.execute_many("import_dois", [import_payload], parallel=False)
    assert imported[0].succeeded
    assert imported[0].value["dois"] == ["10.1000/x"]

    async def approved_write(name, payload, arguments_type):
        parsed_arguments = arguments_type.model_validate(payload)
        orchestrator.approved_tool_call_keys.add(_tool_approval_key(name, parsed_arguments))
        result = await orchestrator.tools.execute_many(name, [payload], parallel=False)
        assert result[0].succeeded, result[0].error
        return result[0].value

    from researchbrain.orchestration.tools import (
        EmbedDocumentArguments,
        ParsePdfArguments,
        QueueFulltextArguments,
    )

    queued = await approved_write(
        "queue_fulltext",
        {
            "library_id": library_id,
            "item_id": queue_item_id,
            "idempotency_key": "fulltext:approval-1",
            "approval_id": "approval-1",
        },
        QueueFulltextArguments,
    )
    with database.session() as session:
        failed_job = session.get(Job, queued["id"])
        failed_job.status = JobStatus.FAILED.value
    retried = await approved_write(
        "queue_fulltext",
        {
            "library_id": library_id,
            "item_id": queue_item_id,
            "idempotency_key": "fulltext:approval-2",
            "approval_id": "approval-2",
        },
        QueueFulltextArguments,
    )
    assert retried["id"] == queued["id"]
    assert retried["requeued"] is True

    parsed_job = await approved_write(
        "parse_pdf",
        {
            "library_id": library_id,
            "item_id": item_id,
            "attachment_id": attachment_id,
            "idempotency_key": "parse:approval-1",
            "approval_id": "approval-1",
        },
        ParsePdfArguments,
    )
    embedded_job = await approved_write(
        "embed_document",
        {
            "library_id": library_id,
            "item_id": item_id,
            "attachment_id": attachment_id,
            "artifact_id": artifact_id,
            "idempotency_key": "embed:approval-1",
            "approval_id": "approval-1",
        },
        EmbedDocumentArguments,
    )
    assert parsed_job["already_complete"] is True
    with database.session() as session:
        session.add(
            DocumentChunk(
                id="existing-vector-chunk",
                artifact_id=artifact_id,
                item_id=item_id,
                attachment_id=attachment_id,
                ordinal=1,
                text="indexed full text",
                content_hash="c" * 64,
                embedding_provider="fixture",
                embedding_model="fixture",
                embedding_dimensions=4,
                index_version="v1",
                index_status="ready",
            )
        )
    already_embedded = await approved_write(
        "embed_document",
        {
            "library_id": library_id,
            "item_id": item_id,
            "attachment_id": attachment_id,
            "artifact_id": artifact_id,
            "idempotency_key": "embed:approval-2",
            "approval_id": "approval-2",
        },
        EmbedDocumentArguments,
    )
    assert already_embedded["already_complete"] is True

    status = await orchestrator.tools.execute_many(
        "job_status",
        [
            {
                "library_id": library_id,
                "job_ids": [queued["id"], embedded_job["id"]],
            }
        ],
        parallel=False,
    )
    assert len(status[0].value["jobs"]) == 2
    exported = await orchestrator.tools.execute_many(
        "export_references",
        [{"library_id": library_id, "item_ids": [item_id], "format": "doi"}],
        parallel=False,
    )
    assert "10.1000/example" in exported[0].value["content"]
    assert any(kind == "approval_required" for kind, _ in events)
    assert any(kind == "tool_result" for kind, _ in events)
    database.engine.dispose()
