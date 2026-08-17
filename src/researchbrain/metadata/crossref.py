from __future__ import annotations

import html
import re
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from researchbrain.domain import CreatorInput, ReferenceRecord, normalize_doi


class MetadataProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _first(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    return str(value or "").strip()


def _clean_abstract(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def _issued_date(message: dict[str, Any]) -> tuple[date | None, int | None]:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if not parts or not parts[0]:
            continue
        values = parts[0]
        try:
            year = int(values[0])
            month = int(values[1]) if len(values) > 1 else 1
            day = int(values[2]) if len(values) > 2 else 1
            return date(year, month, day), year
        except (TypeError, ValueError):
            continue
    return None, None


def crossref_message_to_record(message: dict[str, Any]) -> ReferenceRecord:
    issued, year = _issued_date(message)
    creators = []
    for creator in message.get("author") or []:
        if not isinstance(creator, dict):
            continue
        creators.append(
            CreatorInput(
                given=str(creator.get("given") or "").strip(),
                family=str(creator.get("family") or "").strip(),
                literal=str(creator.get("name") or "").strip(),
                role="author",
            )
        )
    doi = normalize_doi(str(message.get("DOI") or ""))
    crossref_type = str(message.get("type") or "journal-article")
    item_type = {
        "journal-article": "article-journal",
        "proceedings-article": "paper-conference",
        "book-chapter": "chapter",
        "posted-content": "article",
    }.get(crossref_type, crossref_type)
    return ReferenceRecord(
        type=item_type,
        title=_first(message.get("title")),
        abstract=_clean_abstract(str(message.get("abstract") or "")),
        issued=issued,
        year=year,
        container_title=_first(message.get("container-title")),
        volume=str(message.get("volume") or ""),
        issue=str(message.get("issue") or ""),
        pages=str(message.get("page") or message.get("article-number") or ""),
        publisher=str(message.get("publisher") or ""),
        language=str(message.get("language") or ""),
        url=str(message.get("URL") or f"https://doi.org/{doi}"),
        identifiers={"doi": doi},
        creators=creators,
        raw={"crossref": message},
    )


class CrossrefProvider:
    name = "crossref"

    def __init__(self, base_url: str, contact_email: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.contact_email = contact_email
        self._client = client

    async def resolve_doi(self, doi: str) -> ReferenceRecord:
        normalized = normalize_doi(doi)
        params = {"mailto": self.contact_email} if self.contact_email else None
        headers = {"User-Agent": f"ResearchBrain/0.1 (mailto:{self.contact_email or 'not-configured'})"}
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        try:
            response = await client.get(
                f"{self.base_url}/works/{quote(normalized, safe='')}",
                params=params,
                headers=headers,
            )
            if response.status_code == 404:
                raise MetadataProviderError("not_found", f"DOI not found in Crossref: {normalized}")
            response.raise_for_status()
            payload = response.json()
            message = payload.get("message")
            if not isinstance(message, dict):
                raise MetadataProviderError("invalid_response", "Crossref returned no work object")
            return crossref_message_to_record(message)
        except httpx.TimeoutException as exc:
            raise MetadataProviderError("timeout", f"Crossref timed out for {normalized}") from exc
        except httpx.HTTPStatusError as exc:
            raise MetadataProviderError("http_error", f"Crossref HTTP {exc.response.status_code}") from exc
        except (ValueError, TypeError) as exc:
            raise MetadataProviderError("invalid_response", f"Invalid Crossref response: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()
