from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, DocumentArtifact, Job
from researchbrain.domain import CreatorInput, LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository
from researchbrain.tools import ResearchBrainTools


def test_tools_list_get_and_export(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Tools", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(
                title="Tool Paper",
                identifiers={"doi": "10.1000/tool"},
                creators=[CreatorInput(given="Ada", family="Lovelace")],
            ),
            "fixture",
        )
        item_id = item.id
    database.engine.dispose()

    tools = ResearchBrainTools(settings)
    assert tools.list_libraries()[0]["name"] == "Tools"
    assert tools.get_research_context()["libraries"][0]["id"] == library.id
    assert tools.library_status(library.id)["items"] == 1
    assert tools.get_item(item_id)["identifiers"]["doi"] == "10.1000/tool"
    assert tools.export_references([item_id], "doi")["content"] == "10.1000/tool\n"
    batch = tools.import_dois(library.id, ["10.1000/queued"])
    assert batch["accepted"] == 1
    assert tools.list_jobs()[0]["status"] == "queued"
    tools.close()


def test_tools_queue_zotero_and_incremental_index(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Zotero", LibraryMode.ZOTERO_MIRROR)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(title="Indexed Paper", identifiers={"doi": "10.1000/indexed"}),
            "fixture",
        )
        attachment = Attachment(
            item_id=item.id,
            logical_name="paper.pdf",
            object_path="objects/fixture/paper.pdf",
            mime="application/pdf",
            status="stored",
            sha256="a" * 64,
        )
        session.add(attachment)
        session.flush()
        artifact = DocumentArtifact(
            attachment_id=attachment.id,
            source_sha256="a" * 64,
            parser_name="fixture",
            parser_version="1",
            markdown_path="artifacts/fixture/document.md",
            document_json_path="artifacts/fixture/document.json",
            content_hash="b" * 64,
            page_count=2,
            status="ready",
        )
        session.add(artifact)
        session.flush()
        item_id = item.id
    database.engine.dispose()

    tools = ResearchBrainTools(settings)
    sync = tools.sync_zotero(library.id)
    assert sync["status"] == "queued"
    queued = tools.queue_library_index(library.id)
    assert queued["parsed_artifacts"] == 1
    assert queued["document_jobs_pending"] == 1
    status = tools.item_status(item_id)
    assert status["pdf"]["status"] == "ready"
    assert status["parsed"]["status"] == "ready"
    assert status["metadata_embedding"]["status"] == "missing"
    assert status["fulltext_embedding"]["status"] == "missing"
    assert status["next_actions"] == ["queue_library_index"]
    with tools.database.session() as session:
        assert session.query(Job).filter_by(job_type="zotero_sync").count() == 1
        assert session.query(Job).filter_by(job_type="embed_metadata").count() == 1
        assert session.query(Job).filter_by(job_type="embed_document").count() == 1
    tools.close()


async def test_tools_attach_local_pdf_queues_parse(settings, tmp_path):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("PDF", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(title="Local PDF"),
            "fixture",
        )
        item_id = item.id
    database.engine.dispose()
    pdf_path = tmp_path / "local.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nResearchBrain fixture")

    tools = ResearchBrainTools(settings)
    attached = await tools.attach_local_pdf(item_id, str(pdf_path))
    repeated = await tools.attach_local_pdf(item_id, str(pdf_path))
    assert attached["reused"] is False
    assert repeated["reused"] is True
    assert repeated["attachment_id"] == attached["attachment_id"]
    assert tools.item_status(item_id)["pdf"]["status"] == "ready"
    with tools.database.session() as session:
        assert session.query(Job).filter_by(job_type="parse_document").count() == 1
    tools.close()
