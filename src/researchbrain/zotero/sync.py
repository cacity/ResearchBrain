from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from researchbrain.db.base import Database
from researchbrain.db.models import (
    Attachment,
    Collection,
    CollectionItem,
    Creator,
    Item,
    ItemCreator,
    ItemTag,
    Library,
    Tag,
)
from researchbrain.domain import CreatorInput, LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository
from researchbrain.zotero.client import ZoteroPage


class ZoteroReader(Protocol):
    async def fetch_items(self, since: int | None = None) -> ZoteroPage: ...

    async def fetch_collections(self, since: int | None = None) -> ZoteroPage: ...

    async def fetch_deleted(self, since: int) -> ZoteroPage: ...


@dataclass(frozen=True)
class ZoteroSyncResult:
    previous_version: int
    library_version: int
    items_created: int
    items_updated: int
    attachments_linked: int
    tombstones: int


class ZoteroSyncService:
    def __init__(self, database: Database, client: ZoteroReader):
        self.database = database
        self.client = client

    async def sync(self, library_id: str) -> ZoteroSyncResult:
        with self.database.session() as session:
            library = session.get(Library, library_id)
            if not library:
                raise ValueError("library not found")
            if library.mode != LibraryMode.ZOTERO_MIRROR.value:
                raise ValueError("library is not a Zotero mirror")
            previous_version = library.last_version or 0

        since = previous_version if previous_version else None
        collections_page = await self.client.fetch_collections(since)
        items_page = await self.client.fetch_items(since)
        deleted_page = (
            await self.client.fetch_deleted(previous_version) if previous_version else ZoteroPage([], 0)
        )
        observed_versions = [
            version
            for version in (
                collections_page.library_version,
                items_page.library_version,
                deleted_page.library_version if previous_version else None,
            )
            if version is not None and version > 0
        ]
        # Use a conservative watermark so records created while the endpoints
        # are being fetched are picked up by the next incremental sync.
        latest_version = max(previous_version, min(observed_versions, default=previous_version))

        created = updated = attachments = tombstones = 0
        with self.database.session() as session:
            self._sync_collections(session, library_id, collections_page.records)
            regular_items, attachment_items = self._partition_items(items_page.records)
            for record in regular_items:
                _, was_created = self._upsert_item(session, library_id, record)
                created += int(was_created)
                updated += int(not was_created)
            session.flush()
            for record in attachment_items:
                attachments += int(self._upsert_attachment(session, library_id, record))
            tombstones = self._apply_deleted(session, library_id, deleted_page.records)
            library = session.get(Library, library_id)
            if library:
                library.last_version = latest_version

        return ZoteroSyncResult(
            previous_version=previous_version,
            library_version=latest_version,
            items_created=created,
            items_updated=updated,
            attachments_linked=attachments,
            tombstones=tombstones,
        )

    @staticmethod
    def _data(record: dict[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or record)
        data.setdefault("key", record.get("key"))
        data.setdefault("version", record.get("version"))
        return data

    def _sync_collections(self, session: Session, library_id: str, records: list[dict[str, Any]]) -> None:
        for record in records:
            data = self._data(record)
            key = str(data.get("key") or "")
            if not key:
                continue
            collection = session.scalar(
                select(Collection)
                .where(Collection.library_id == library_id)
                .where(Collection.source_key == key)
            )
            if not collection:
                collection = Collection(library_id=library_id, source_key=key, name="")
                session.add(collection)
            collection.name = str(data.get("name") or "Untitled")
            collection.source_version = _integer(data.get("version"))

    @staticmethod
    def _partition_items(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        regular: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        for record in records:
            data = dict(record.get("data") or record)
            if data.get("itemType") == "attachment":
                attachments.append(record)
            elif data.get("itemType") != "note":
                regular.append(record)
        return regular, attachments

    def _upsert_item(self, session: Session, library_id: str, record: dict[str, Any]) -> tuple[Item, bool]:
        data = self._data(record)
        source_key = str(data.get("key") or "")
        if not source_key:
            raise ValueError("Zotero item has no key")
        item = session.scalar(
            select(Item).where(Item.library_id == library_id).where(Item.source_key == source_key)
        )
        doi = str(data.get("DOI") or "").strip()
        reference = _zotero_record(data)
        if not item and doi:
            try:
                candidate = LibraryRepository(session).find_item_by_identifier(
                    library_id, "doi", reference.identifiers["doi"]
                )
                if candidate and candidate.source_key in {None, source_key}:
                    item = candidate
            except (KeyError, ValueError):
                item = None
        created = item is None
        if created:
            item, _ = LibraryRepository(session).add_reference(
                library_id,
                reference,
                "zotero",
                deduplicate=False,
            )
        else:
            self._replace_item_fields(session, item, reference)
        item.source_key = source_key
        item.source_version = _integer(data.get("version"))
        item.status = "active"
        item.deleted_at = None
        item.raw_data = {"zotero": data}
        session.flush()
        self._replace_memberships(session, item, data)
        return item, created

    @staticmethod
    def _replace_item_fields(session: Session, item: Item, record: ReferenceRecord) -> None:
        item.item_type = record.type
        item.title = record.title
        item.abstract = record.abstract
        item.year = record.year
        item.issued = record.issued.isoformat() if record.issued else None
        item.container_title = record.container_title
        item.volume = record.volume
        item.issue = record.issue
        item.pages = record.pages
        item.publisher = record.publisher
        item.language = record.language
        item.url = record.url
        old_links = list(session.scalars(select(ItemCreator).where(ItemCreator.item_id == item.id)))
        old_creator_ids = [link.creator_id for link in old_links]
        session.execute(delete(ItemCreator).where(ItemCreator.item_id == item.id))
        for creator_id in old_creator_ids:
            session.execute(delete(Creator).where(Creator.id == creator_id))
        for position, creator_input in enumerate(record.creators):
            creator = Creator(
                given=creator_input.given,
                family=creator_input.family,
                literal=creator_input.literal,
            )
            session.add(creator)
            session.flush()
            session.add(
                ItemCreator(
                    item_id=item.id,
                    creator_id=creator.id,
                    role=creator_input.role,
                    position=position,
                )
            )

    @staticmethod
    def _replace_memberships(session: Session, item: Item, data: dict[str, Any]) -> None:
        session.execute(delete(CollectionItem).where(CollectionItem.item_id == item.id))
        for collection_key in data.get("collections") or []:
            collection = session.scalar(
                select(Collection)
                .where(Collection.library_id == item.library_id)
                .where(Collection.source_key == str(collection_key))
            )
            if collection:
                session.add(CollectionItem(collection_id=collection.id, item_id=item.id))
        session.execute(delete(ItemTag).where(ItemTag.item_id == item.id))
        for raw_tag in data.get("tags") or []:
            name = str(raw_tag.get("tag") if isinstance(raw_tag, dict) else raw_tag).strip()
            if not name:
                continue
            tag = session.scalar(select(Tag).where(Tag.library_id == item.library_id).where(Tag.name == name))
            if not tag:
                tag = Tag(library_id=item.library_id, name=name)
                session.add(tag)
                session.flush()
            session.add(ItemTag(item_id=item.id, tag_id=tag.id, source="zotero"))

    def _upsert_attachment(self, session: Session, library_id: str, record: dict[str, Any]) -> bool:
        data = self._data(record)
        parent_key = str(data.get("parentItem") or "")
        source_key = str(data.get("key") or "")
        mime = str(data.get("contentType") or "").lower()
        attachment_path = str(data.get("path") or "")
        filename = str(data.get("filename") or "")
        is_pdf = mime == "application/pdf" or attachment_path.lower().endswith(".pdf")
        is_pdf = is_pdf or filename.lower().endswith(".pdf")
        if not is_pdf:
            return False
        parent = session.scalar(
            select(Item).where(Item.library_id == library_id).where(Item.source_key == parent_key)
        )
        if not parent or not source_key:
            return False
        attachment = session.scalar(
            select(Attachment)
            .where(Attachment.item_id == parent.id)
            .where(Attachment.source_key == source_key)
        )
        created = attachment is None
        if not attachment:
            attachment = Attachment(item_id=parent.id, source_key=source_key, logical_name="")
            session.add(attachment)
        attachment.source_version = _integer(data.get("version"))
        attachment.logical_name = str(data.get("title") or data.get("filename") or "Attachment")
        attachment.object_path = attachment_path
        attachment.mime = mime
        attachment.source_url = str(data.get("url") or "")
        attachment.status = "linked"
        return created

    @staticmethod
    def _apply_deleted(session: Session, library_id: str, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        payload = records[0]
        keys = [str(key) for key in payload.get("items") or []]
        count = 0
        for key in keys:
            item = session.scalar(
                select(Item).where(Item.library_id == library_id).where(Item.source_key == key)
            )
            if item:
                item.status = "tombstone"
                item.deleted_at = datetime.now(UTC)
                count += 1
            attachments = list(
                session.scalars(
                    select(Attachment)
                    .join(Item, Item.id == Attachment.item_id)
                    .where(Item.library_id == library_id)
                    .where(Attachment.source_key == key)
                )
            )
            for attachment in attachments:
                attachment.status = "missing"
                count += 1
        return count


def _zotero_record(data: dict[str, Any]) -> ReferenceRecord:
    raw_date = str(data.get("date") or "")
    year_match = re.search(r"(?:^|\D)((?:18|19|20|21)\d{2})(?:\D|$)", raw_date)
    year = int(year_match.group(1)) if year_match else None
    creators = [
        CreatorInput(
            given=str(creator.get("firstName") or ""),
            family=str(creator.get("lastName") or ""),
            literal=str(creator.get("name") or ""),
            role=str(creator.get("creatorType") or "author"),
        )
        for creator in data.get("creators") or []
        if isinstance(creator, dict)
    ]
    identifiers = {}
    if data.get("DOI"):
        identifiers["doi"] = str(data["DOI"])
    if data.get("ISBN"):
        identifiers["isbn"] = str(data["ISBN"])
    if data.get("ISSN"):
        identifiers["issn"] = str(data["ISSN"])
    return ReferenceRecord(
        type=_zotero_type(str(data.get("itemType") or "document")),
        title=str(data.get("title") or "Untitled"),
        abstract=str(data.get("abstractNote") or ""),
        year=year,
        container_title=str(data.get("publicationTitle") or data.get("proceedingsTitle") or ""),
        volume=str(data.get("volume") or ""),
        issue=str(data.get("issue") or ""),
        pages=str(data.get("pages") or ""),
        publisher=str(data.get("publisher") or ""),
        language=str(data.get("language") or ""),
        url=str(data.get("url") or ""),
        identifiers=identifiers,
        creators=creators,
        raw={"zotero": data},
    )


def _zotero_type(item_type: str) -> str:
    return {
        "journalArticle": "article-journal",
        "conferencePaper": "paper-conference",
        "bookSection": "chapter",
        "book": "book",
        "thesis": "thesis",
        "report": "report",
        "preprint": "article",
    }.get(item_type, "document")


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
