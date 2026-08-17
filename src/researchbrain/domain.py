from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any
from urllib.parse import unquote

from pydantic import BaseModel, Field, field_validator

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def normalize_doi(raw: str) -> str:
    value = unquote(raw.strip()).strip()
    value = re.sub(r"^doi\s*:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
    value = value.strip().strip("\"'<>").rstrip(".,;:")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    while value.endswith("]") and value.count("]") > value.count("["):
        value = value[:-1]
    if not DOI_PATTERN.fullmatch(value):
        raise ValueError(f"invalid DOI: {raw!r}")
    return value.lower()


class LibraryMode(StrEnum):
    STANDALONE = "standalone"
    ZOTERO_MIRROR = "zotero_mirror"


class ItemStatus(StrEnum):
    ACTIVE = "active"
    TRASHED = "trashed"
    TOMBSTONE = "tombstone"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    REVIEW_REQUIRED = "review_required"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELED = "canceled"


class JobType(StrEnum):
    RESOLVE_METADATA = "resolve_metadata"
    RESOLVE_FULLTEXT = "resolve_fulltext"
    PARSE_DOCUMENT = "parse_document"
    EMBED_DOCUMENT = "embed_document"
    EMBED_METADATA = "embed_metadata"
    REBUILD_INDEX = "rebuild_index"
    ZOTERO_SYNC = "zotero_sync"


class CreatorInput(BaseModel):
    given: str = ""
    family: str = ""
    literal: str = ""
    role: str = "author"


class ReferenceRecord(BaseModel):
    type: str = "article-journal"
    title: str
    abstract: str = ""
    issued: date | None = None
    year: int | None = None
    container_title: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    language: str = ""
    url: str = ""
    identifiers: dict[str, str] = Field(default_factory=dict)
    creators: list[CreatorInput] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def title_must_not_be_empty(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("title is required")
        return value

    @field_validator("identifiers")
    @classmethod
    def normalize_identifiers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized = {key.lower(): str(identifier).strip() for key, identifier in value.items() if identifier}
        if "doi" in normalized:
            normalized["doi"] = normalize_doi(normalized["doi"])
        return normalized


class DoiImportRequest(BaseModel):
    library_id: str
    dois: list[str] = Field(min_length=1, max_length=5000)
    include_si: bool
    collection_id: str | None = None


class LibraryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: LibraryMode = LibraryMode.STANDALONE
