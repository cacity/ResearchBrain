import httpx
import pytest

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, Item, Job
from researchbrain.domain import LibraryMode
from researchbrain.library.repository import LibraryRepository
from researchbrain.zotero.attachments import ZoteroAttachmentImporter
from researchbrain.zotero.client import ZoteroLocalClient, ZoteroPage
from researchbrain.zotero.sync import ZoteroSyncService


class FixtureZotero:
    def __init__(self):
        self.incremental = False

    async def fetch_collections(self, since=None):
        return ZoteroPage(
            [{"key": "C1", "version": 2, "data": {"name": "Storms"}}] if not self.incremental else [],
            2 if not self.incremental else 3,
        )

    async def fetch_items(self, since=None):
        if self.incremental:
            return ZoteroPage([], 3)
        item = {
            "key": "ITEM1",
            "version": 2,
            "data": {
                "itemType": "journalArticle",
                "title": "A Zotero Paper",
                "date": "2025-07-01",
                "DOI": "10.1000/zotero",
                "publicationTitle": "Journal",
                "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
                "collections": ["C1"],
                "tags": [{"tag": "geomagnetism"}],
            },
        }
        attachment = {
            "key": "PDF1",
            "version": 2,
            "data": {
                "itemType": "attachment",
                "parentItem": "ITEM1",
                "title": "Full Text PDF",
                "contentType": "application/pdf",
                "path": "C:/Users/stark/Zotero/storage/ABCD/paper.pdf",
            },
        }
        return ZoteroPage([item, attachment], 2)

    async def fetch_deleted(self, since):
        return ZoteroPage([{"items": ["ITEM1"]}] if self.incremental else [], 3)


@pytest.mark.asyncio
async def test_local_api_missing_deleted_endpoint_is_treated_as_empty():
    transport = httpx.MockTransport(lambda _request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        page = await ZoteroLocalClient("http://zotero.invalid/api", client).fetch_deleted(27)

    assert page == ZoteroPage(records=[], library_version=27)


class IncrementalAttachmentZotero:
    def __init__(self):
        self.phase = 1
        self.requested_since: list[int | None] = []

    async def fetch_collections(self, since=None):
        self.requested_since.append(since)
        return ZoteroPage([], 2 if self.phase == 1 else 3)

    async def fetch_items(self, since=None):
        self.requested_since.append(since)
        if self.phase == 1:
            return ZoteroPage(
                [
                    {
                        "key": "ITEM1",
                        "version": 2,
                        "data": {
                            "itemType": "journalArticle",
                            "title": "Initial item",
                            "date": "2025",
                        },
                    }
                ],
                3,
            )
        return ZoteroPage(
            [
                {
                    "key": "ITEM2",
                    "version": 3,
                    "data": {
                        "itemType": "journalArticle",
                        "title": "Incremental item",
                        "date": "2026",
                    },
                },
                {
                    "key": "PDF2",
                    "version": 3,
                    "data": {
                        "itemType": "attachment",
                        "parentItem": "ITEM2",
                        "title": "Incremental.pdf",
                        "contentType": "application/pdf",
                        "path": "storage:incremental.pdf",
                    },
                },
            ],
            3,
        )

    async def fetch_deleted(self, since):
        self.requested_since.append(since)
        return ZoteroPage([], 3)


@pytest.mark.asyncio
async def test_zotero_initial_and_incremental_sync(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Zotero", LibraryMode.ZOTERO_MIRROR)
        library_id = library.id

    fixture = FixtureZotero()
    first = await ZoteroSyncService(database, fixture).sync(library_id)
    assert first.items_created == 1
    assert first.attachments_linked == 1
    assert first.library_version == 2
    with database.session() as session:
        item = session.query(Item).filter_by(library_id=library_id).one()
        assert item.title == "A Zotero Paper"
        assert session.query(Attachment).filter_by(item_id=item.id).one().status == "linked"

    fixture.incremental = True
    second = await ZoteroSyncService(database, fixture).sync(library_id)
    assert second.previous_version == 2
    assert second.tombstones == 1
    with database.session() as session:
        item = session.query(Item).filter_by(library_id=library_id).one()
        assert item.status == "tombstone"
    database.engine.dispose()


@pytest.mark.asyncio
async def test_new_item_and_pdf_are_fetched_incrementally(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    zotero_root = settings.data_dir / "zotero-source"
    source_dir = zotero_root / "storage" / "PDF2"
    source_dir.mkdir(parents=True)
    (source_dir / "incremental.pdf").write_bytes(b"%PDF-1.7\nincremental fixture")
    with database.session() as session:
        library = LibraryRepository(session).create_library("Zotero", LibraryMode.ZOTERO_MIRROR)
        library_id = library.id

    fixture = IncrementalAttachmentZotero()
    first = await ZoteroSyncService(database, fixture).sync(library_id)
    assert first.items_created == 1
    assert first.library_version == 2

    fixture.phase = 2
    second = await ZoteroSyncService(database, fixture).sync(library_id)
    imported = await ZoteroAttachmentImporter(
        database,
        settings.data_dir,
        zotero_root,
    ).import_pending(library_id)

    assert second.previous_version == 2
    assert second.items_created == 1
    assert second.attachments_linked == 1
    assert fixture.requested_since[-3:] == [2, 2, 2]
    assert imported.imported == 1
    with database.session() as session:
        assert session.query(Item).filter_by(library_id=library_id).count() == 2
        attachment = session.query(Attachment).filter_by(source_key="PDF2").one()
        assert attachment.status == "stored"
        assert session.query(Job).filter_by(job_type="parse_document").count() == 1
    database.engine.dispose()


@pytest.mark.asyncio
async def test_distinct_zotero_keys_with_same_doi_keep_their_attachments(settings):
    class DuplicateDoiZotero:
        async def fetch_collections(self, since=None):
            return ZoteroPage([], 4)

        async def fetch_items(self, since=None):
            records = []
            for item_key, pdf_key in (("ITEM1", "PDF1"), ("ITEM2", "PDF2")):
                records.extend(
                    [
                        {
                            "key": item_key,
                            "version": 4,
                            "data": {
                                "itemType": "journalArticle",
                                "title": item_key,
                                "DOI": "10.1000/shared-zotero",
                            },
                        },
                        {
                            "key": pdf_key,
                            "version": 4,
                            "data": {
                                "itemType": "attachment",
                                "parentItem": item_key,
                                "title": f"{pdf_key}.pdf",
                                "contentType": "application/pdf",
                                "path": f"storage:{pdf_key}.pdf",
                            },
                        },
                    ]
                )
            return ZoteroPage(records, 4)

        async def fetch_deleted(self, since):
            return ZoteroPage([], 4)

    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Zotero", LibraryMode.ZOTERO_MIRROR)
        library_id = library.id

    result = await ZoteroSyncService(database, DuplicateDoiZotero()).sync(library_id)

    assert result.items_created == 2
    assert result.attachments_linked == 2
    with database.session() as session:
        items = session.query(Item).filter_by(library_id=library_id).all()
        assert {item.source_key for item in items} == {"ITEM1", "ITEM2"}
        attachments = session.query(Attachment).all()
        assert {attachment.source_key for attachment in attachments} == {"PDF1", "PDF2"}
        assert len({attachment.item_id for attachment in attachments}) == 2
    database.engine.dispose()


@pytest.mark.asyncio
async def test_zotero_storage_pdf_is_copied_and_queued(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    zotero_root = settings.data_dir / "zotero-source"
    source_dir = zotero_root / "storage" / "PDFKEY"
    source_dir.mkdir(parents=True)
    (source_dir / "paper.pdf").write_bytes(b"%PDF-1.7\nzotero fixture")
    with database.session() as session:
        library = LibraryRepository(session).create_library("Zotero", LibraryMode.ZOTERO_MIRROR)
        item = Item(library_id=library.id, source_key="ITEM1", title="Stored paper")
        session.add(item)
        session.flush()
        attachment = Attachment(
            item_id=item.id,
            source_key="PDFKEY",
            logical_name="paper.pdf",
            object_path="storage:paper.pdf",
            mime="application/pdf",
            status="linked",
        )
        session.add(attachment)
        session.flush()
        library_id = library.id
        attachment_id = attachment.id

    result = await ZoteroAttachmentImporter(
        database,
        settings.data_dir,
        zotero_root,
    ).import_pending(library_id)

    assert result.imported == 1
    with database.session() as session:
        attachment = session.get(Attachment, attachment_id)
        job = session.query(Job).filter_by(job_type="parse_document").one()
        assert attachment is not None
        assert attachment.status == "stored"
        assert attachment.sha256
        assert (settings.data_dir / attachment.object_path).is_file()
        assert job.payload["attachment_id"] == attachment_id

    (source_dir / "paper.pdf").write_bytes(b"%PDF-1.7\nreplacement fixture")
    with database.session() as session:
        attachment = session.get(Attachment, attachment_id)
        assert attachment is not None
        attachment.object_path = "storage:paper.pdf"
        attachment.status = "linked"

    replacement = await ZoteroAttachmentImporter(
        database,
        settings.data_dir,
        zotero_root,
    ).import_pending(library_id)
    assert replacement.imported == 1
    with database.session() as session:
        parse_jobs = session.query(Job).filter_by(job_type="parse_document").all()
        assert len(parse_jobs) == 2
        assert len({job.idempotency_key for job in parse_jobs}) == 2
    database.engine.dispose()
