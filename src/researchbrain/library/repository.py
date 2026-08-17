from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from researchbrain.db.models import (
    Creator,
    Identifier,
    Item,
    ItemCreator,
    Library,
    MetadataProvenance,
)
from researchbrain.domain import LibraryMode, ReferenceRecord


class LibraryRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_library(self, name: str, mode: LibraryMode) -> Library:
        library = Library(name=name.strip(), mode=mode.value)
        self.session.add(library)
        self.session.flush()
        return library

    def get_library(self, library_id: str) -> Library | None:
        return self.session.get(Library, library_id)

    def list_libraries(self) -> list[Library]:
        return list(self.session.scalars(select(Library).order_by(Library.created_at)))

    def find_item_by_identifier(self, library_id: str, scheme: str, normalized_value: str) -> Item | None:
        statement = (
            select(Item)
            .join(Identifier)
            .where(Item.library_id == library_id)
            .where(Identifier.scheme == scheme.lower())
            .where(Identifier.normalized_value == normalized_value)
            .options(selectinload(Item.identifiers))
        )
        return self.session.scalar(statement)

    def get_item(self, item_id: str) -> Item | None:
        statement = (
            select(Item)
            .where(Item.id == item_id)
            .options(
                selectinload(Item.identifiers),
                selectinload(Item.creators).selectinload(ItemCreator.creator),
            )
        )
        return self.session.scalar(statement)

    def find_item_by_title_year(self, library_id: str, title: str, year: int | None) -> Item | None:
        normalized = " ".join(title.split()).strip()
        if not normalized:
            return None
        statement = select(Item).where(Item.library_id == library_id).where(Item.title == normalized)
        if year is not None:
            statement = statement.where(Item.year == year)
        return self.session.scalar(statement.options(selectinload(Item.identifiers)))

    def add_reference(
        self,
        library_id: str,
        record: ReferenceRecord,
        provider: str,
        deduplicate: bool = True,
    ) -> tuple[Item, bool]:
        if deduplicate:
            for scheme in ("doi", "pmid", "pmcid", "arxiv"):
                identifier = record.identifiers.get(scheme)
                if identifier:
                    existing = self.find_item_by_identifier(library_id, scheme, identifier)
                    if existing:
                        self._merge_missing_fields(existing, record, provider)
                        return existing, False
            if not record.identifiers:
                existing = self.find_item_by_title_year(library_id, record.title, record.year)
                if existing:
                    self._merge_missing_fields(existing, record, provider)
                    return existing, False

        item = Item(
            library_id=library_id,
            item_type=record.type,
            title=record.title,
            abstract=record.abstract,
            year=record.year,
            issued=record.issued.isoformat() if record.issued else None,
            container_title=record.container_title,
            volume=record.volume,
            issue=record.issue,
            pages=record.pages,
            publisher=record.publisher,
            language=record.language,
            url=record.url,
            raw_data=record.raw,
        )
        self.session.add(item)
        self.session.flush()

        for position, (scheme, value) in enumerate(record.identifiers.items()):
            self.session.add(
                Identifier(
                    library_id=library_id,
                    item_id=item.id,
                    scheme=scheme,
                    normalized_value=value,
                    is_primary=position == 0,
                )
            )

        for position, creator_input in enumerate(record.creators):
            creator = Creator(
                given=creator_input.given,
                family=creator_input.family,
                literal=creator_input.literal,
            )
            self.session.add(creator)
            self.session.flush()
            self.session.add(
                ItemCreator(
                    item_id=item.id,
                    creator_id=creator.id,
                    role=creator_input.role,
                    position=position,
                )
            )

        self._record_provenance(item.id, provider, record)
        self.session.flush()
        return item, True

    def _merge_missing_fields(self, item: Item, record: ReferenceRecord, provider: str) -> None:
        values = {
            "abstract": record.abstract,
            "year": record.year,
            "container_title": record.container_title,
            "volume": record.volume,
            "issue": record.issue,
            "pages": record.pages,
            "publisher": record.publisher,
            "language": record.language,
            "url": record.url,
        }
        for field_name, value in values.items():
            if not getattr(item, field_name) and value:
                setattr(item, field_name, value)
        existing = {value.scheme: value.normalized_value for value in item.identifiers}
        for scheme, value in record.identifiers.items():
            if scheme not in existing:
                self.session.add(
                    Identifier(
                        library_id=item.library_id,
                        item_id=item.id,
                        scheme=scheme,
                        normalized_value=value,
                        is_primary=False,
                    )
                )
        if record.raw:
            item.raw_data = {**(item.raw_data or {}), **record.raw}
        self._record_provenance(item.id, provider, record)
        self.session.flush()

    def _record_provenance(self, item_id: str, provider: str, record: ReferenceRecord) -> None:
        values = {
            "title": record.title,
            "abstract": record.abstract,
            "year": record.year,
            "container_title": record.container_title,
            "publisher": record.publisher,
            "identifiers": record.identifiers,
            "creators": [creator.model_dump() for creator in record.creators],
        }
        for field_name, value in values.items():
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            value_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            existing = self.session.scalar(
                select(MetadataProvenance.id)
                .where(MetadataProvenance.item_id == item_id)
                .where(MetadataProvenance.field_name == field_name)
                .where(MetadataProvenance.provider == provider)
                .where(MetadataProvenance.value_hash == value_hash)
            )
            if existing:
                continue
            self.session.add(
                MetadataProvenance(
                    item_id=item_id,
                    field_name=field_name,
                    provider=provider,
                    value_hash=value_hash,
                    raw_value={"value": value},
                )
            )
