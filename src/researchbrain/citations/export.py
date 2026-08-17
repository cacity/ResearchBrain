from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from researchbrain.db.models import Item, ItemCreator


class CitationExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportArtifact:
    filename: str
    mime: str
    content: str


class CitationExporter:
    def __init__(self, session):
        self.session = session

    def export(
        self,
        item_ids: list[str],
        output_format: Literal["csl-json", "bibtex", "ris", "doi", "markdown"],
    ) -> ExportArtifact:
        items = self._items(item_ids)
        if output_format == "csl-json":
            content = json.dumps([_to_csl(item) for item in items], ensure_ascii=False, indent=2)
            return ExportArtifact("references.json", "application/json", content)
        if output_format == "doi":
            dois = [_identifier(item, "doi") for item in items]
            content = "\n".join(value for value in dois if value) + "\n"
            return ExportArtifact("dois.txt", "text/plain", content)
        if output_format == "markdown":
            content = "\n".join(f"- {_markdown_reference(item)}" for item in items) + "\n"
            return ExportArtifact("references.md", "text/markdown", content)
        if output_format == "bibtex":
            return ExportArtifact("references.bib", "application/x-bibtex", _bibtex(items))
        if output_format == "ris":
            return ExportArtifact("references.ris", "application/x-research-info-systems", _ris(items))
        raise CitationExportError(f"unsupported export format: {output_format}")

    def _items(self, item_ids: list[str]) -> list[Item]:
        if not item_ids:
            return []
        statement = (
            select(Item)
            .where(Item.id.in_(item_ids))
            .options(
                selectinload(Item.identifiers),
                selectinload(Item.creators).selectinload(ItemCreator.creator),
            )
        )
        found = {item.id: item for item in self.session.scalars(statement)}
        missing = [item_id for item_id in item_ids if item_id not in found]
        if missing:
            raise CitationExportError(f"items not found: {', '.join(missing)}")
        return [found[item_id] for item_id in item_ids]


def _to_csl(item: Item) -> dict:
    result = {
        "id": item.id,
        "type": item.item_type,
        "title": item.title,
        "abstract": item.abstract,
        "container-title": item.container_title,
        "volume": item.volume,
        "issue": item.issue,
        "page": item.pages,
        "publisher": item.publisher,
        "language": item.language,
        "URL": item.url,
        "author": [
            {
                "given": link.creator.given,
                "family": link.creator.family,
                **({"literal": link.creator.literal} if link.creator.literal else {}),
            }
            for link in sorted(item.creators, key=lambda value: value.position)
            if link.role == "author"
        ],
    }
    if item.year:
        result["issued"] = {"date-parts": [[item.year]]}
    doi = _identifier(item, "doi")
    if doi:
        result["DOI"] = doi
    return {key: value for key, value in result.items() if value not in ("", None, [])}


def _bibtex(items: list[Item]) -> str:
    try:
        import bibtexparser
        from bibtexparser.bwriter import BibTexWriter
    except ImportError as exc:
        raise CitationExportError("BibTeX export component is not installed") from exc
    entries = []
    for item in items:
        entry = {
            "ENTRYTYPE": _bibtex_type(item.item_type),
            "ID": _citation_key(item),
            "title": item.title,
            "author": " and ".join(
                _creator_name(link)
                for link in sorted(item.creators, key=lambda value: value.position)
                if link.role == "author"
            ),
            "year": str(item.year or ""),
            "journal": item.container_title,
            "volume": item.volume,
            "number": item.issue,
            "pages": item.pages,
            "publisher": item.publisher,
            "doi": _identifier(item, "doi"),
            "url": item.url,
        }
        entries.append({key: value for key, value in entry.items() if value})
    database = bibtexparser.bibdatabase.BibDatabase()
    database.entries = entries
    writer = BibTexWriter()
    writer.indent = "  "
    return writer.write(database)


def _ris(items: list[Item]) -> str:
    try:
        import rispy
    except ImportError as exc:
        raise CitationExportError("RIS export component is not installed") from exc
    records = []
    for item in items:
        records.append(
            {
                "type_of_reference": _ris_type(item.item_type),
                "title": item.title,
                "authors": [
                    _creator_name(link)
                    for link in sorted(item.creators, key=lambda value: value.position)
                    if link.role == "author"
                ],
                "year": str(item.year or ""),
                "journal_name": item.container_title,
                "volume": item.volume,
                "number": item.issue,
                **_ris_pages(item.pages),
                "doi": _identifier(item, "doi"),
                "urls": [item.url] if item.url else [],
                "abstract": item.abstract,
            }
        )
    return rispy.dumps(records)


def _markdown_reference(item: Item) -> str:
    authors = ", ".join(
        _creator_name(link)
        for link in sorted(item.creators, key=lambda value: value.position)
        if link.role == "author"
    )
    parts = [authors, f"({item.year})" if item.year else "", item.title]
    if item.container_title:
        parts.append(f"*{item.container_title}*")
    if item.volume:
        parts.append(item.volume + (f"({item.issue})" if item.issue else ""))
    if item.pages:
        parts.append(item.pages)
    doi = _identifier(item, "doi")
    if doi:
        parts.append(f"https://doi.org/{doi}")
    return ". ".join(part.strip().rstrip(".") for part in parts if part).strip() + "."


def _identifier(item: Item, scheme: str) -> str:
    return next(
        (value.normalized_value for value in item.identifiers if value.scheme == scheme),
        "",
    )


def _creator_name(link: ItemCreator) -> str:
    creator = link.creator
    return creator.literal or ", ".join(value for value in (creator.family, creator.given) if value)


def _citation_key(item: Item) -> str:
    first_author = next(
        (link.creator.family for link in sorted(item.creators, key=lambda value: value.position)),
        "item",
    )
    title_word = next(iter(re.findall(r"[A-Za-z0-9]+", item.title)), "reference")
    base = f"{first_author}{item.year or 'nd'}{title_word}"
    return re.sub(r"[^A-Za-z0-9_-]", "", base) + item.id[:6]


def _bibtex_type(item_type: str) -> str:
    return {
        "article-journal": "article",
        "paper-conference": "inproceedings",
        "chapter": "incollection",
        "book": "book",
        "thesis": "phdthesis",
        "report": "techreport",
    }.get(item_type, "misc")


def _ris_type(item_type: str) -> str:
    return {
        "article-journal": "JOUR",
        "paper-conference": "CPAPER",
        "chapter": "CHAP",
        "book": "BOOK",
        "thesis": "THES",
        "report": "RPRT",
    }.get(item_type, "GEN")


def _ris_pages(pages: str) -> dict[str, str]:
    values = re.split(r"\s*[-–—]\s*", pages, maxsplit=1)
    if not values or not values[0]:
        return {}
    result = {"start_page": values[0]}
    if len(values) > 1 and values[1]:
        result["end_page"] = values[1]
    return result
