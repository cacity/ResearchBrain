from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

from researchbrain.domain import normalize_doi
from researchbrain.metadata.crossref import crossref_message_to_record


@dataclass(frozen=True)
class DiscoveryRecord:
    source: str
    source_id: str
    title: str
    authors: list[str]
    year: int | None
    venue: str
    abstract: str
    doi: str
    url: str
    sources: list[str] | None = None
    identifiers: dict[str, str] | None = None
    is_oa: bool = False
    fulltext_url: str = ""
    publication_type: str = "article-journal"

    def __post_init__(self) -> None:
        if self.sources is None:
            object.__setattr__(self, "sources", [self.source])
        if self.identifiers is None:
            identifiers = {self.source: self.source_id} if self.source_id else {}
            if self.doi:
                identifiers["doi"] = self.doi
            object.__setattr__(self, "identifiers", identifiers)


@dataclass(frozen=True)
class ProviderStatus:
    source: str
    status: str
    count: int
    elapsed_ms: int
    error: str = ""


@dataclass(frozen=True)
class DiscoverySearchResult:
    records: list[DiscoveryRecord]
    providers: list[ProviderStatus]


class DiscoveryProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list[DiscoveryRecord]: ...


class CrossrefSearchProvider:
    name = "crossref"

    def __init__(self, base_url: str, email: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self._client = client

    async def search(self, query: str, limit: int) -> list[DiscoveryRecord]:
        response = await _get(
            self._client,
            f"{self.base_url}/works",
            {"query": query, "rows": limit, **({"mailto": self.email} if self.email else {})},
        )
        records = []
        for message in (response.json().get("message") or {}).get("items") or []:
            try:
                reference = crossref_message_to_record(message)
            except ValueError:
                continue
            records.append(
                DiscoveryRecord(
                    source=self.name,
                    source_id=reference.identifiers.get("doi", ""),
                    title=reference.title,
                    authors=[
                        creator.literal or f"{creator.given} {creator.family}".strip()
                        for creator in reference.creators
                    ],
                    year=reference.year,
                    venue=reference.container_title,
                    abstract=reference.abstract,
                    doi=reference.identifiers.get("doi", ""),
                    url=reference.url,
                    identifiers=reference.identifiers,
                    is_oa=False,
                    fulltext_url="",
                    publication_type=reference.type,
                )
            )
        return records


class OpenAlexSearchProvider:
    name = "openalex"

    def __init__(
        self,
        email: str,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
    ):
        self.email = email
        self.api_key = api_key
        self._client = client

    async def search(self, query: str, limit: int) -> list[DiscoveryRecord]:
        params: dict[str, Any] = {"search": query, "per-page": limit}
        if self.email:
            params["mailto"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        response = await _get(self._client, "https://api.openalex.org/works", params)
        records = []
        for work in response.json().get("results") or []:
            doi = _safe_doi(str(work.get("doi") or ""))
            authors = [
                str((authorship.get("author") or {}).get("display_name") or "")
                for authorship in work.get("authorships") or []
            ]
            primary = work.get("primary_location") or {}
            source = primary.get("source") or {}
            best_oa = work.get("best_oa_location") or {}
            open_access = work.get("open_access") or {}
            openalex_id = str(work.get("id") or "")
            identifiers = {"openalex": openalex_id.rsplit("/", 1)[-1]} if openalex_id else {}
            if doi:
                identifiers["doi"] = doi
            records.append(
                DiscoveryRecord(
                    source=self.name,
                    source_id=openalex_id,
                    title=str(work.get("display_name") or "Untitled"),
                    authors=[value for value in authors if value],
                    year=_integer(work.get("publication_year")),
                    venue=str(source.get("display_name") or ""),
                    abstract=_openalex_abstract(work.get("abstract_inverted_index")),
                    doi=doi,
                    url=str(primary.get("landing_page_url") or openalex_id),
                    identifiers=identifiers,
                    is_oa=bool(open_access.get("is_oa")),
                    fulltext_url=str(best_oa.get("pdf_url") or ""),
                    publication_type=_publication_type(str(work.get("type") or "")),
                )
            )
        return records


class ArxivSearchProvider:
    name = "arxiv"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def search(self, query: str, limit: int) -> list[DiscoveryRecord]:
        response = await _get(
            self._client,
            "https://export.arxiv.org/api/query",
            {"search_query": f"all:{query}", "start": 0, "max_results": limit},
        )
        root = ET.fromstring(response.text)
        namespace = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        records = []
        for entry in root.findall("atom:entry", namespace):
            entry_url = _xml_text(entry, "atom:id", namespace)
            arxiv_id = entry_url.rstrip("/").rsplit("/", 1)[-1]
            published = _xml_text(entry, "atom:published", namespace)
            doi = _safe_doi(_xml_text(entry, "arxiv:doi", namespace))
            identifiers = {"arxiv": arxiv_id}
            if doi:
                identifiers["doi"] = doi
            records.append(
                DiscoveryRecord(
                    source=self.name,
                    source_id=arxiv_id,
                    title=_clean_space(_xml_text(entry, "atom:title", namespace)) or "Untitled",
                    authors=[
                        _xml_text(author, "atom:name", namespace)
                        for author in entry.findall("atom:author", namespace)
                    ],
                    year=_integer(published[:4]),
                    venue="arXiv",
                    abstract=_clean_space(_xml_text(entry, "atom:summary", namespace)),
                    doi=doi,
                    url=entry_url,
                    identifiers=identifiers,
                    is_oa=True,
                    fulltext_url=f"https://arxiv.org/pdf/{arxiv_id}",
                    publication_type="article",
                )
            )
        return records


class PubMedSearchProvider:
    name = "pubmed"

    def __init__(
        self,
        email: str,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
    ):
        self.email = email
        self.api_key = api_key
        self._client = client

    async def search(self, query: str, limit: int) -> list[DiscoveryRecord]:
        common = {"tool": "ResearchBrain", **({"email": self.email} if self.email else {})}
        if self.api_key:
            common["api_key"] = self.api_key
        searched = await _get(
            self._client,
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            {"db": "pubmed", "term": query, "retmode": "json", "retmax": limit, **common},
        )
        ids = (searched.json().get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return []
        fetched = await _get(
            self._client,
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", **common},
        )
        return _parse_pubmed_xml(fetched.text)


class LiteratureDiscovery:
    def __init__(self, providers: list[DiscoveryProvider]):
        self.providers = providers

    async def search(self, query: str, limit_per_source: int = 10) -> list[DiscoveryRecord]:
        return (await self.search_with_status(query, limit_per_source)).records

    async def search_with_status(
        self,
        query: str,
        limit_per_source: int = 10,
    ) -> DiscoverySearchResult:
        async def run(provider: DiscoveryProvider) -> tuple[list[DiscoveryRecord], ProviderStatus]:
            started = time.perf_counter()
            try:
                records = await provider.search(query, limit_per_source)
                status = ProviderStatus(
                    provider.name,
                    "complete",
                    len(records),
                    round((time.perf_counter() - started) * 1000),
                )
                return records, status
            except Exception as exc:
                status = ProviderStatus(
                    provider.name,
                    "failed",
                    0,
                    round((time.perf_counter() - started) * 1000),
                    _provider_error(exc),
                )
                return [], status

        results = await asyncio.gather(*(run(provider) for provider in self.providers))
        combined = [record for records, _ in results for record in records]
        return DiscoverySearchResult(
            _merge_records(combined),
            [status for _, status in results],
        )


async def _get(client: httpx.AsyncClient | None, url: str, params: dict[str, Any]) -> httpx.Response:
    owns_client = client is None
    resolved = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        for attempt in range(3):
            try:
                response = await resolved.get(
                    url,
                    params=params,
                    headers={"User-Agent": "ResearchBrain/0.1 (+https://github.com/)"},
                )
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }
                if not retryable or attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))
        raise RuntimeError("unreachable HTTP retry state")
    finally:
        if owns_client:
            await resolved.aclose()


def _parse_pubmed_xml(text: str) -> list[DiscoveryRecord]:
    root = ET.fromstring(text)
    records: list[DiscoveryRecord] = []
    for entry in root.findall(".//PubmedArticle"):
        citation = entry.find("MedlineCitation")
        article = citation.find("Article") if citation is not None else None
        if citation is None or article is None:
            continue
        pmid = _element_text(citation.find("PMID"))
        title = _element_text(article.find("ArticleTitle")) or "Untitled"
        authors = []
        for author in article.findall("./AuthorList/Author"):
            collective = _element_text(author.find("CollectiveName"))
            literal = " ".join(
                value
                for value in (
                    _element_text(author.find("ForeName")),
                    _element_text(author.find("LastName")),
                )
                if value
            )
            if collective or literal:
                authors.append(collective or literal)
        abstracts = []
        for abstract in article.findall("./Abstract/AbstractText"):
            value = _element_text(abstract)
            label = str(abstract.get("Label") or "").strip()
            if value:
                abstracts.append(f"{label}: {value}" if label else value)
        identifiers = {"pmid": pmid} if pmid else {}
        doi = ""
        for article_id in entry.findall("./PubmedData/ArticleIdList/ArticleId"):
            scheme = str(article_id.get("IdType") or "").lower()
            value = _element_text(article_id)
            if not value:
                continue
            if scheme == "doi":
                doi = _safe_doi(value)
                if doi:
                    identifiers["doi"] = doi
            elif scheme in {"pmc", "pmcid"}:
                identifiers["pmcid"] = value
        journal = article.find("Journal")
        venue = _element_text(journal.find("Title")) if journal is not None else ""
        publication_date = journal.find("./JournalIssue/PubDate") if journal is not None else None
        year = None
        if publication_date is not None:
            year = _integer(_element_text(publication_date.find("Year")))
            if year is None:
                year = _year_from_text(_element_text(publication_date.find("MedlineDate")))
        pmcid = identifiers.get("pmcid", "")
        records.append(
            DiscoveryRecord(
                source="pubmed",
                source_id=pmid,
                title=_clean_space(title),
                authors=authors,
                year=year,
                venue=venue,
                abstract="\n".join(abstracts),
                doi=doi,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                identifiers=identifiers,
                is_oa=bool(pmcid),
                fulltext_url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/" if pmcid else "",
                publication_type="article-journal",
            )
        )
    return records


def _merge_records(records: list[DiscoveryRecord]) -> list[DiscoveryRecord]:
    unique: list[DiscoveryRecord] = []
    aliases: dict[str, int] = {}
    for record in records:
        keys = _record_keys(record)
        matched = next((aliases[key] for key in keys if key in aliases), None)
        if matched is None:
            matched = len(unique)
            unique.append(record)
        else:
            unique[matched] = _merge_record(unique[matched], record)
        for key in _record_keys(unique[matched]):
            aliases[key] = matched
    return unique


def _record_keys(record: DiscoveryRecord) -> list[str]:
    identifiers = record.identifiers or {}
    keys = [
        f"{scheme}:{value.lower()}"
        for scheme in ("doi", "pmid", "pmcid", "arxiv", "openalex")
        if (value := identifiers.get(scheme))
    ]
    if record.doi:
        keys.insert(0, f"doi:{record.doi.lower()}")
    title = _clean_space(record.title).lower()
    if title:
        keys.append(f"title:{title}:{record.year or ''}")
        keys.append(f"title:{title}")
    return keys


def _merge_record(left: DiscoveryRecord, right: DiscoveryRecord) -> DiscoveryRecord:
    identifiers = {**(left.identifiers or {}), **(right.identifiers or {})}
    sources = list(dict.fromkeys([*(left.sources or [left.source]), *(right.sources or [right.source])]))
    abstract = max((left.abstract, right.abstract), key=len)
    authors = left.authors if len(left.authors) >= len(right.authors) else right.authors
    return replace(
        left,
        sources=sources,
        identifiers=identifiers,
        authors=authors,
        year=left.year or right.year,
        venue=left.venue or right.venue,
        abstract=abstract,
        doi=left.doi or right.doi,
        url=left.url or right.url,
        is_oa=left.is_oa or right.is_oa,
        fulltext_url=left.fulltext_url or right.fulltext_url,
        publication_type=left.publication_type or right.publication_type,
    )


def _openalex_abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, raw_positions in index.items():
        if isinstance(raw_positions, list):
            positions.extend((int(position), str(word)) for position in raw_positions)
    return " ".join(word for _, word in sorted(positions))


def _element_text(element: ET.Element | None) -> str:
    return _clean_space("".join(element.itertext())) if element is not None else ""


def _xml_text(element: ET.Element, path: str, namespace: dict[str, str]) -> str:
    found = element.find(path, namespace)
    return found.text.strip() if found is not None and found.text else ""


def _safe_doi(value: str) -> str:
    try:
        return normalize_doi(value)
    except ValueError:
        return ""


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _year_from_text(value: str) -> int | None:
    match = re.search(r"(?:18|19|20|21)\d{2}", value)
    return int(match.group()) if match else None


def _clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _publication_type(value: str) -> str:
    return {
        "article": "article-journal",
        "preprint": "article",
        "book-chapter": "chapter",
        "proceedings-article": "paper-conference",
    }.get(value, value or "article-journal")


def _provider_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    return str(exc)[:300] or exc.__class__.__name__
