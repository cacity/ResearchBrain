from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

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
    attempts: int = 1
    degraded_reason: str = ""


@dataclass(frozen=True)
class DiscoveryMergeReport:
    canonical_key: str
    match_keys: list[str]
    sources: list[str]
    identifiers: dict[str, str]
    title: str
    merged_count: int
    retained_source: str
    retained_reason: str


@dataclass(frozen=True)
class DiscoverySearchResult:
    records: list[DiscoveryRecord]
    providers: list[ProviderStatus]
    merge_report: list[DiscoveryMergeReport] = field(default_factory=list)
    ranking_report: list[dict[str, Any]] = field(default_factory=list)
    seed_report: list[dict[str, Any]] = field(default_factory=list)


class DiscoveryProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list[DiscoveryRecord]: ...


@runtime_checkable
class SeedExpansionProvider(Protocol):
    name: str

    async def expand_seed(self, record: DiscoveryRecord, limit: int) -> list[DiscoveryRecord]: ...


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
        return [_openalex_work_to_record(work) for work in response.json().get("results") or []]

    async def expand_seed(self, record: DiscoveryRecord, limit: int) -> list[DiscoveryRecord]:
        openalex_id = (record.identifiers or {}).get("openalex") or record.source_id.rsplit("/", 1)[-1]
        if not openalex_id:
            return []
        params: dict[str, Any] = {}
        if self.email:
            params["mailto"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        work_key = openalex_id.rsplit("/", 1)[-1]
        work_url = f"https://api.openalex.org/works/{work_key}"
        work = (await _get(self._client, work_url, params)).json()
        records: list[DiscoveryRecord] = []
        referenced = [str(value) for value in work.get("referenced_works") or [] if value]
        for reference_url in referenced[: max(0, limit // 2)]:
            try:
                referenced_response = await _get(self._client, reference_url, params)
                records.append(_openalex_work_to_record(referenced_response.json()))
            except Exception:  # noqa: BLE001 - a missing referenced work should not fail the seed expansion
                continue
        citing_limit = max(0, limit - len(records))
        if citing_limit:
            citing_params = {
                **params,
                "filter": f"cites:{openalex_id.rsplit('/', 1)[-1]}",
                "per-page": citing_limit,
            }
            citing = await _get(self._client, "https://api.openalex.org/works", citing_params)
            records.extend(_openalex_work_to_record(work) for work in citing.json().get("results") or [])
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
    def __init__(
        self,
        providers: list[DiscoveryProvider],
        *,
        provider_retries: int = 1,
        provider_timeout_seconds: float = 18.0,
    ):
        self.providers = providers
        self.provider_retries = max(1, provider_retries)
        self.provider_timeout_seconds = max(0.01, provider_timeout_seconds)

    async def search(self, query: str, limit_per_source: int = 10) -> list[DiscoveryRecord]:
        return (await self.search_with_status(query, limit_per_source)).records

    async def search_with_status(
        self,
        query: str,
        limit_per_source: int = 10,
        sources: list[str] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        track_citations: bool = False,
        seed_limit: int = 6,
    ) -> DiscoverySearchResult:
        selected = {value.strip().lower() for value in (sources or []) if value and value.strip()}
        providers = [provider for provider in self.providers if not selected or provider.name in selected]

        async def run(provider: DiscoveryProvider) -> tuple[list[DiscoveryRecord], ProviderStatus]:
            started = time.perf_counter()
            last_error = ""
            for attempt in range(1, self.provider_retries + 1):
                try:
                    records = await asyncio.wait_for(
                        provider.search(query, limit_per_source),
                        timeout=self.provider_timeout_seconds,
                    )
                    elapsed = round((time.perf_counter() - started) * 1000)
                    degraded = attempt > 1
                    status = ProviderStatus(
                        provider.name,
                        "degraded" if degraded else "complete",
                        len(records),
                        elapsed,
                        "" if not degraded else last_error,
                        attempt,
                        "succeeded after provider-level retry" if degraded else "",
                    )
                    return records, status
                except Exception as exc:  # noqa: BLE001 - source failures are isolated per provider
                    last_error = _provider_error(exc)
                    if attempt < self.provider_retries:
                        await asyncio.sleep(0.2 * (2 ** (attempt - 1)))
            status = ProviderStatus(
                provider.name,
                "failed",
                0,
                round((time.perf_counter() - started) * 1000),
                last_error,
                self.provider_retries,
                "provider unavailable after retries",
            )
            return [], status

        results = await asyncio.gather(*(run(provider) for provider in providers))
        statuses = [status for _, status in results]
        combined = [record for records, _ in results for record in records]
        seed_report: list[dict[str, Any]] = []
        if track_citations and combined:
            seed_records = _rank_records(combined, query)[:seed_limit]
            expanded, expansion_statuses, seed_report = await self._expand_seed_records(
                seed_records, seed_limit
            )
            combined.extend(expanded)
            statuses.extend(expansion_statuses)
        merged, merge_report = _merge_records_with_report(combined)
        if year_from is not None:
            merged = [record for record in merged if record.year is not None and record.year >= year_from]
        if year_to is not None:
            merged = [record for record in merged if record.year is not None and record.year <= year_to]
        ranked = _rank_records(merged, query)
        ranking_report = [_ranking_report(record, query, index) for index, record in enumerate(ranked, 1)]
        return DiscoverySearchResult(
            ranked,
            statuses,
            merge_report,
            ranking_report,
            seed_report,
        )

    async def _expand_seed_records(
        self, seeds: list[DiscoveryRecord], seed_limit: int
    ) -> tuple[list[DiscoveryRecord], list[ProviderStatus], list[dict[str, Any]]]:
        providers = [provider for provider in self.providers if isinstance(provider, SeedExpansionProvider)]
        if not providers:
            return [], [], []
        expanded: list[DiscoveryRecord] = []
        statuses: list[ProviderStatus] = []
        report: list[dict[str, Any]] = []
        per_seed_limit = max(1, seed_limit)
        for seed in seeds:
            for provider in providers:
                started = time.perf_counter()
                try:
                    records = await provider.expand_seed(seed, per_seed_limit)
                    expanded.extend(records)
                    elapsed = round((time.perf_counter() - started) * 1000)
                    statuses.append(
                        ProviderStatus(
                            f"{provider.name}:seed_expansion",
                            "complete",
                            len(records),
                            elapsed,
                        )
                    )
                    report.append(
                        {
                            "seed_title": seed.title,
                            "seed_identifiers": seed.identifiers or {},
                            "provider": provider.name,
                            "added_candidates": len(records),
                            "relations": ["references", "citations"],
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - citation expansion is best-effort
                    statuses.append(
                        ProviderStatus(
                            f"{provider.name}:seed_expansion",
                            "failed",
                            0,
                            round((time.perf_counter() - started) * 1000),
                            _provider_error(exc),
                            degraded_reason="seed reference/citation expansion failed",
                        )
                    )
        return expanded, statuses, report


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


def _rank_records(records: list[DiscoveryRecord], query: str) -> list[DiscoveryRecord]:
    return sorted(
        records,
        key=lambda record: _ranking_score(record, query),
        reverse=True,
    )


def _ranking_score(record: DiscoveryRecord, query: str) -> tuple[float, int, int, int]:
    terms = _query_terms(query)
    text = f"{record.title} {record.abstract}".casefold()
    title = record.title.casefold()
    term_hits = sum(1 for term in terms if term in text)
    title_hits = sum(1 for term in terms if term in title)
    recency = record.year or 0
    traceability = len(record.identifiers or {}) + int(bool(record.url)) + int(bool(record.abstract))
    return (term_hits + title_hits * 0.5, recency, traceability, len(record.abstract))


def _ranking_report(record: DiscoveryRecord, query: str, rank: int) -> dict[str, Any]:
    relevance, recency, traceability, abstract_length = _ranking_score(record, query)
    return {
        "rank": rank,
        "title": record.title,
        "identifiers": record.identifiers or {},
        "sources": record.sources or [record.source],
        "year": record.year,
        "relevance_score": relevance,
        "recency_score": recency,
        "traceability_score": traceability,
        "abstract_length": abstract_length,
        "sort": "relevance_then_time_then_traceability",
    }


def _query_terms(query: str) -> list[str]:
    return [
        value.casefold()
        for value in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,}", query)
        if value.casefold() not in {"and", "or", "not", "the", "for", "with"}
    ]


def _merge_records(records: list[DiscoveryRecord]) -> list[DiscoveryRecord]:
    return _merge_records_with_report(records)[0]


def _merge_records_with_report(
    records: list[DiscoveryRecord],
) -> tuple[list[DiscoveryRecord], list[DiscoveryMergeReport]]:
    unique: list[DiscoveryRecord] = []
    groups: list[list[DiscoveryRecord]] = []
    aliases: dict[str, int] = {}
    for record in records:
        keys = _record_keys(record)
        matched = next((aliases[key] for key in keys if key in aliases), None)
        if matched is None:
            matched = len(unique)
            unique.append(record)
            groups.append([record])
        else:
            groups[matched].append(record)
            unique[matched] = _merge_record(unique[matched], record)
        for key in _record_keys(unique[matched]):
            aliases[key] = matched
        for key in keys:
            aliases[key] = matched
    return unique, [_merge_report(record, group) for record, group in zip(unique, groups, strict=True)]


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
    primary, secondary = _preferred_record(left, right)
    identifiers = {**(secondary.identifiers or {}), **(primary.identifiers or {})}
    if secondary.doi and "doi" not in identifiers:
        identifiers["doi"] = secondary.doi
    if primary.doi:
        identifiers["doi"] = primary.doi
    sources = list(dict.fromkeys([*(left.sources or [left.source]), *(right.sources or [right.source])]))
    abstract = max((left.abstract, right.abstract), key=len)
    authors = primary.authors if primary.authors else secondary.authors
    title = primary.title if primary.title and primary.title != "Untitled" else secondary.title
    return replace(
        primary,
        sources=sources,
        identifiers=identifiers,
        title=title,
        authors=authors,
        year=primary.year or secondary.year,
        venue=primary.venue or secondary.venue,
        abstract=abstract,
        doi=primary.doi or secondary.doi,
        url=primary.url or secondary.url,
        is_oa=left.is_oa or right.is_oa,
        fulltext_url=primary.fulltext_url or secondary.fulltext_url,
        publication_type=primary.publication_type or secondary.publication_type,
    )


def _preferred_record(
    left: DiscoveryRecord, right: DiscoveryRecord
) -> tuple[DiscoveryRecord, DiscoveryRecord]:
    if _record_completeness(right) > _record_completeness(left):
        return right, left
    return left, right


def _record_completeness(record: DiscoveryRecord) -> tuple[int, int, int, int, int, int, int]:
    identifiers = record.identifiers or {}
    return (
        int(bool(record.abstract)),
        len(identifiers),
        int(bool(record.authors)),
        int(record.year is not None),
        int(bool(record.venue)),
        int(bool(record.url)),
        len(record.abstract),
    )


def _merge_report(record: DiscoveryRecord, group: list[DiscoveryRecord]) -> DiscoveryMergeReport:
    match_keys = sorted({key for value in group for key in _record_keys(value) if _is_cross_source_key(key)})
    canonical_key = _canonical_merge_key(record)
    return DiscoveryMergeReport(
        canonical_key=canonical_key,
        match_keys=match_keys or [canonical_key],
        sources=list(record.sources or [record.source]),
        identifiers=dict(record.identifiers or {}),
        title=record.title,
        merged_count=len(group),
        retained_source=record.source,
        retained_reason=_retained_reason(record),
    )


def _is_cross_source_key(key: str) -> bool:
    return key.startswith(("doi:", "pmid:", "arxiv:", "title:"))


def _canonical_merge_key(record: DiscoveryRecord) -> str:
    identifiers = record.identifiers or {}
    for scheme in ("doi", "pmid", "arxiv"):
        if value := identifiers.get(scheme):
            return f"{scheme}:{value.lower()}"
    normalized = _clean_space(record.title).lower()
    return f"title:{normalized}" if normalized else f"{record.source}:{record.source_id.lower()}"


def _retained_reason(record: DiscoveryRecord) -> str:
    traits = []
    if record.abstract:
        traits.append("has abstract")
    if record.identifiers:
        traits.append("traceable identifiers")
    if record.url:
        traits.append("source URL")
    if record.authors and record.year:
        traits.append("bibliographic details")
    return ", ".join(traits) or "first matching record"


def _openalex_work_to_record(work: dict[str, Any]) -> DiscoveryRecord:
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
    return DiscoveryRecord(
        source="openalex",
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
