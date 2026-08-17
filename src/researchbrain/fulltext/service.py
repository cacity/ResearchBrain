from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, Item
from researchbrain.fulltext.discovery import (
    FullTextCandidate,
    FullTextProvider,
    FullTextProviderError,
    crossref_open_candidates,
)
from researchbrain.fulltext.storage import DownloadError, ObjectStore, StoredObject, validate_remote_url


@dataclass(frozen=True)
class FullTextResult:
    attachments_created: int
    candidates_found: int
    attachment_ids: list[str]


class FullTextPipeline:
    def __init__(
        self,
        database: Database,
        provider: FullTextProvider,
        object_store: ObjectStore,
        landing_client: httpx.AsyncClient | None = None,
    ):
        self.database = database
        self.provider = provider
        self.object_store = object_store
        self.landing_client = landing_client

    async def process(self, item_id: str, doi: str, include_si: bool) -> FullTextResult:
        with self.database.session() as session:
            item = session.get(Item, item_id)
            if not item:
                raise ValueError("item not found")
            raw_data = dict(item.raw_data)

        provider_error = None
        try:
            candidates = await self.provider.discover(doi) if doi else []
        except FullTextProviderError as exc:
            provider_error = exc
            candidates = []
        candidates.extend(crossref_open_candidates(raw_data))
        candidates.extend(_discovery_open_candidates(raw_data))
        candidates = _deduplicate_candidates(candidates)
        if not candidates and provider_error:
            raise provider_error

        attachment_ids = []
        created_count = 0
        candidates_found = len(candidates)
        main_candidates = [candidate for candidate in candidates if candidate.kind == "main"]
        expanded_main: list[FullTextCandidate] = []
        for candidate in main_candidates:
            expanded = await self._expand_candidate(candidate)
            candidates_found += max(0, len(expanded) - 1)
            expanded_main.extend(expanded)
        stored = await self._download_first(expanded_main)
        if stored:
            attachment_id, created = self._record_attachment(item_id, stored)
            attachment_ids.append(attachment_id)
            created_count += int(created)

        if include_si:
            expanded_si: list[FullTextCandidate] = []
            for candidate in (value for value in candidates if value.kind == "supplement"):
                expanded_si.extend(await self._expand_candidate(candidate))
            for candidate in expanded_si:
                stored = await self._download_first([candidate])
                if stored:
                    attachment_id, created = self._record_attachment(item_id, stored)
                else:
                    continue
                attachment_ids.append(attachment_id)
                created_count += int(created)
        return FullTextResult(
            attachments_created=created_count,
            candidates_found=candidates_found,
            attachment_ids=attachment_ids,
        )

    async def _expand_candidate(self, candidate: FullTextCandidate) -> list[FullTextCandidate]:
        if candidate.access != "landing":
            return [candidate]
        validate_remote_url(candidate.url)
        owns_client = self.landing_client is None
        client = self.landing_client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        try:
            response = await client.get(
                candidate.url,
                headers={"Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9"},
            )
            response.raise_for_status()
            validate_remote_url(str(response.url))
            if len(response.content) > 5 * 1024 * 1024:
                return []
            content_type = response.headers.get("content-type", "").lower()
            if "application/pdf" in content_type or response.content.startswith(b"%PDF-"):
                return [replace(candidate, url=str(response.url), access="pdf")]
            return pdf_candidates_from_html(response.content, str(response.url), candidate)
        except (httpx.HTTPError, ValueError):
            return []
        finally:
            if owns_client:
                await client.aclose()

    async def _download_first(
        self,
        candidates: list[FullTextCandidate],
    ) -> tuple[StoredObject, FullTextCandidate] | None:
        errors: list[DownloadError] = []
        for candidate in candidates:
            try:
                stored = await self.object_store.download_pdf(candidate)
                return stored, candidate
            except DownloadError as exc:
                errors.append(exc)
        transient = next(
            (
                error
                for error in errors
                if error.code in {"timeout", "http_error", "download_error", "storage_error"}
            ),
            None,
        )
        if transient:
            raise transient
        return None

    def _record_attachment(
        self,
        item_id: str,
        downloaded: tuple[StoredObject, FullTextCandidate],
    ) -> tuple[str, bool]:
        stored, resolved = downloaded
        with self.database.session() as session:
            existing = session.scalar(
                select(Attachment)
                .where(Attachment.item_id == item_id)
                .where(Attachment.sha256 == stored.sha256)
            )
            if existing:
                return existing.id, False
            attachment = Attachment(
                item_id=item_id,
                sha256=stored.sha256,
                logical_name="Supplement.pdf" if resolved.kind == "supplement" else "Full Text.pdf",
                object_path=str(stored.path.relative_to(self.object_store.data_dir)),
                mime=stored.mime,
                source_url=resolved.url,
                license=resolved.license,
                status="stored",
                bytes=stored.bytes,
            )
            session.add(attachment)
            session.flush()
            return attachment.id, True


def _deduplicate_candidates(candidates: list[FullTextCandidate]) -> list[FullTextCandidate]:
    unique: dict[str, FullTextCandidate] = {}
    for candidate in sorted(candidates, key=lambda value: value.priority):
        unique.setdefault(candidate.url.rstrip("/").lower(), candidate)
    return list(unique.values())


def _discovery_open_candidates(raw_data: dict) -> list[FullTextCandidate]:
    discovery = raw_data.get("discovery") if isinstance(raw_data, dict) else None
    if not isinstance(discovery, dict) or not discovery.get("is_oa"):
        return []
    url = str(discovery.get("fulltext_url") or "").strip()
    if not url:
        return []
    return [
        FullTextCandidate(
            url=url,
            provider="discovery",
            license="open-access-link",
            version="unknown",
            evidence="source_marked_open_access",
            priority=20,
        )
    ]


class _PdfLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            if name in {"citation_pdf_url", "eprints.document_url", "wkhealth_pdf_url"}:
                self.links.append((values.get("content", ""), name))
        if tag.lower() == "a":
            self._href = values.get("href", "")
            self._text = []
            if values.get("type", "").lower() == "application/pdf" or "download" in values:
                self.links.append((self._href, "pdf attribute"))

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        text = " ".join(self._text).strip().lower()
        path = urlparse(self._href).path.lower()
        if (
            path.endswith(".pdf")
            or "/pdf" in path
            or re.search(r"\b(pdf|download pdf|view pdf|full text)\b", text)
        ):
            self.links.append((self._href, f"anchor={text[:80]}"))
        self._href = ""
        self._text = []


def pdf_candidates_from_html(
    body: bytes,
    base_url: str,
    source: FullTextCandidate,
) -> list[FullTextCandidate]:
    charset_match = re.search(rb"charset=[\"']?([A-Za-z0-9._-]+)", body[:8192], re.IGNORECASE)
    charset = charset_match.group(1).decode("ascii", "ignore") if charset_match else "utf-8"
    try:
        page = body.decode(charset, "replace")
    except LookupError:
        page = body.decode("utf-8", "replace")
    parser = _PdfLinkParser()
    parser.feed(page)
    candidates: list[FullTextCandidate] = []
    for href, evidence in parser.links:
        url = urljoin(base_url, html.unescape(href.strip()))
        if not url:
            continue
        try:
            validate_remote_url(url)
        except DownloadError:
            continue
        candidates.append(
            replace(
                source,
                url=url,
                evidence=f"{source.evidence};landing:{evidence}",
                access="pdf",
            )
        )
    return _deduplicate_candidates(candidates)[:20]
