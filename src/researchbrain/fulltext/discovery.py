from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from researchbrain.domain import normalize_doi


class FullTextProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FullTextCandidate:
    url: str
    provider: str
    license: str
    version: str
    kind: str = "main"
    evidence: str = ""
    priority: int = 100
    access: str = "pdf"


class FullTextProvider(Protocol):
    name: str

    async def discover(self, doi: str) -> list[FullTextCandidate]: ...


class MultiSourceFullTextProvider:
    name = "multi-source-oa"

    def __init__(self, providers: list[FullTextProvider]):
        self.providers = providers

    async def discover(self, doi: str) -> list[FullTextCandidate]:
        candidates: list[FullTextCandidate] = []
        errors: list[FullTextProviderError] = []
        results = await asyncio.gather(
            *(provider.discover(doi) for provider in self.providers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, FullTextProviderError):
                errors.append(result)
            elif isinstance(result, Exception):
                errors.append(FullTextProviderError("provider_error", str(result)))
            else:
                candidates.extend(result)
        if candidates:
            return _deduplicate_candidates(candidates)
        if errors:
            missing_contact = next(
                (error for error in errors if error.code == "contact_email_missing"),
                None,
            )
            raise missing_contact or errors[0]
        return []


class UnpaywallProvider:
    name = "unpaywall"

    def __init__(self, base_url: str, email: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self._client = client

    async def discover(self, doi: str) -> list[FullTextCandidate]:
        normalized = normalize_doi(doi)
        if not self.email:
            raise FullTextProviderError("contact_email_missing", "Unpaywall requires a contact email")
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        try:
            response = await client.get(
                f"{self.base_url}/{quote(normalized, safe='')}",
                params={"email": self.email},
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            payload = response.json()
            return _unpaywall_candidates(payload)
        except httpx.TimeoutException as exc:
            raise FullTextProviderError("timeout", f"Unpaywall timed out for {normalized}") from exc
        except httpx.HTTPStatusError as exc:
            raise FullTextProviderError(
                "http_error", f"Unpaywall HTTP {exc.response.status_code} for {normalized}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise FullTextProviderError("invalid_response", f"Invalid Unpaywall response: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()


def _unpaywall_candidates(payload: dict[str, Any]) -> list[FullTextCandidate]:
    if not payload.get("is_oa"):
        return []
    locations = []
    best = payload.get("best_oa_location")
    if isinstance(best, dict):
        locations.append(best)
    locations.extend(location for location in payload.get("oa_locations") or [] if isinstance(location, dict))
    candidates: list[FullTextCandidate] = []
    seen: set[str] = set()
    for position, location in enumerate(locations):
        url = str(location.get("url_for_pdf") or "").strip()
        landing_url = str(location.get("url_for_landing_page") or "").strip()
        host_type = str(location.get("host_type") or "")
        priority = position + (0 if host_type == "repository" else 10)
        if url and url not in seen:
            seen.add(url)
            candidates.append(
                FullTextCandidate(
                    url=url,
                    provider="unpaywall",
                    license=str(location.get("license") or "unknown"),
                    version=str(location.get("version") or "unknown"),
                    evidence="unpaywall_is_oa",
                    priority=priority,
                )
            )
        if landing_url and landing_url not in seen:
            seen.add(landing_url)
            candidates.append(
                FullTextCandidate(
                    url=landing_url,
                    provider="unpaywall",
                    license=str(location.get("license") or "unknown"),
                    version=str(location.get("version") or "unknown"),
                    evidence="unpaywall_oa_landing_page",
                    priority=50 + priority,
                    access="landing",
                )
            )
    return sorted(candidates, key=lambda candidate: candidate.priority)


class OpenAlexFullTextProvider:
    name = "openalex"

    def __init__(
        self,
        email: str = "",
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
    ):
        self.email = email
        self.api_key = api_key
        self._client = client

    async def discover(self, doi: str) -> list[FullTextCandidate]:
        normalized = normalize_doi(doi)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        params = {}
        if self.email:
            params["mailto"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        try:
            work_id = quote(f"https://doi.org/{normalized}", safe="")
            response = await client.get(f"https://api.openalex.org/works/{work_id}", params=params)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return _openalex_candidates(response.json())
        except httpx.TimeoutException as exc:
            raise FullTextProviderError("timeout", f"OpenAlex timed out for {normalized}") from exc
        except httpx.HTTPStatusError as exc:
            raise FullTextProviderError(
                "http_error", f"OpenAlex HTTP {exc.response.status_code} for {normalized}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise FullTextProviderError("invalid_response", f"Invalid OpenAlex response: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()


def _openalex_candidates(payload: dict[str, Any]) -> list[FullTextCandidate]:
    locations: list[dict[str, Any]] = []
    best = payload.get("best_oa_location")
    if isinstance(best, dict):
        locations.append(best)
    locations.extend(location for location in payload.get("locations") or [] if isinstance(location, dict))
    candidates: list[FullTextCandidate] = []
    for position, location in enumerate(locations):
        license_name = str(location.get("license") or "")
        if not (location.get("is_oa") or license_name or location is best):
            continue
        version = str(location.get("version") or "unknown")
        pdf_url = str(location.get("pdf_url") or "").strip()
        landing_url = str(location.get("landing_page_url") or "").strip()
        source = location.get("source") if isinstance(location.get("source"), dict) else {}
        repository = str(source.get("display_name") or "unknown")
        if pdf_url:
            candidates.append(
                FullTextCandidate(
                    url=pdf_url,
                    provider="openalex",
                    license=license_name or "open-access",
                    version=version,
                    evidence=f"openalex_oa_location:{repository}",
                    priority=20 + position,
                )
            )
        if landing_url:
            candidates.append(
                FullTextCandidate(
                    url=landing_url,
                    provider="openalex",
                    license=license_name or "open-access",
                    version=version,
                    evidence=f"openalex_oa_landing_page:{repository}",
                    priority=70 + position,
                    access="landing",
                )
            )
    return _deduplicate_candidates(candidates)


class PmcFullTextProvider:
    name = "pmc"

    def __init__(
        self,
        email: str = "",
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
    ):
        self.email = email
        self.api_key = api_key
        self._client = client

    async def discover(self, doi: str) -> list[FullTextCandidate]:
        normalized = normalize_doi(doi)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        params = {"ids": normalized, "format": "json", "tool": "researchbrain"}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        try:
            response = await client.get(
                "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
                params=params,
            )
            response.raise_for_status()
            return _pmc_candidates(response.json())
        except httpx.TimeoutException as exc:
            raise FullTextProviderError("timeout", f"PMC timed out for {normalized}") from exc
        except httpx.HTTPStatusError as exc:
            raise FullTextProviderError(
                "http_error", f"PMC HTTP {exc.response.status_code} for {normalized}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise FullTextProviderError("invalid_response", f"Invalid PMC response: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()


def _pmc_candidates(payload: dict[str, Any]) -> list[FullTextCandidate]:
    candidates: list[FullTextCandidate] = []
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        pmcid = str(record.get("pmcid") or "").strip().upper()
        if not pmcid.startswith("PMC") or not pmcid[3:].isdigit():
            continue
        base_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}"
        candidates.extend(
            [
                FullTextCandidate(
                    url=f"{base_url}/pdf/",
                    provider="pmc",
                    license="pmc-open-access",
                    version="publishedVersion",
                    evidence=f"ncbi_doi_converter:{pmcid}",
                    priority=10,
                ),
                FullTextCandidate(
                    url=f"{base_url}/",
                    provider="pmc",
                    license="pmc-open-access",
                    version="publishedVersion",
                    evidence=f"ncbi_doi_converter:{pmcid}",
                    priority=60,
                    access="landing",
                ),
            ]
        )
    return candidates


def _deduplicate_candidates(candidates: list[FullTextCandidate]) -> list[FullTextCandidate]:
    unique: dict[str, FullTextCandidate] = {}
    for candidate in sorted(candidates, key=lambda value: value.priority):
        unique.setdefault(candidate.url.rstrip("/").lower(), candidate)
    return list(unique.values())


def crossref_open_candidates(raw_data: dict[str, Any]) -> list[FullTextCandidate]:
    message = raw_data.get("crossref") if isinstance(raw_data, dict) else None
    if not isinstance(message, dict):
        return []
    licenses = [
        str(entry.get("URL") or "") for entry in message.get("license") or [] if isinstance(entry, dict)
    ]
    open_license = next((license_url for license_url in licenses if _is_open_license(license_url)), "")
    if not open_license:
        return []
    candidates = []
    for position, link in enumerate(message.get("link") or []):
        if not isinstance(link, dict):
            continue
        url = str(link.get("URL") or "").strip()
        content_type = str(link.get("content-type") or "").lower()
        if not url or "pdf" not in content_type:
            continue
        label = " ".join(
            str(link.get(key) or "") for key in ("content-version", "intended-application", "URL")
        )
        kind = "supplement" if "supp" in label.lower() else "main"
        candidates.append(
            FullTextCandidate(
                url=url,
                provider="crossref",
                license=open_license,
                version=str(link.get("content-version") or "unknown"),
                kind=kind,
                evidence="crossref_open_license",
                priority=30 + position,
            )
        )
    return candidates


def _is_open_license(value: str) -> bool:
    lowered = value.lower()
    return "creativecommons.org/licenses/" in lowered or "creativecommons.org/publicdomain/" in lowered
