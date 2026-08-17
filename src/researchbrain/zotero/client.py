from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class ZoteroConnectionError(RuntimeError):
    pass


class ZoteroEndpointUnavailableError(ZoteroConnectionError):
    pass


@dataclass(frozen=True)
class ZoteroPage:
    records: list[dict[str, Any]]
    library_version: int


class ZoteroLocalClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:23119/api",
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def probe(self) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=3.0)
        try:
            response = await client.get(f"{self.base_url}/")
            response.raise_for_status()
            return {
                "available": True,
                "api_version": response.headers.get("Zotero-API-Version", "3"),
                "library_version": _header_version(response),
            }
        except (httpx.HTTPError, OSError) as exc:
            raise ZoteroConnectionError(f"Zotero Local API unavailable: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def fetch_items(self, since: int | None = None) -> ZoteroPage:
        return await self._fetch_paginated("/users/0/items", since)

    async def fetch_collections(self, since: int | None = None) -> ZoteroPage:
        return await self._fetch_paginated("/users/0/collections", since)

    async def fetch_deleted(self, since: int) -> ZoteroPage:
        try:
            response = await self._get("/users/0/deleted", {"since": since})
        except ZoteroEndpointUnavailableError:
            # Zotero Desktop's Local API does not expose this Web API endpoint.
            return ZoteroPage(records=[], library_version=since)
        payload = response.json()
        records = [payload] if isinstance(payload, dict) else []
        return ZoteroPage(records=records, library_version=_header_version(response, since))

    async def _fetch_paginated(self, path: str, since: int | None) -> ZoteroPage:
        records: list[dict[str, Any]] = []
        start = 0
        limit = 100
        latest_version = since or 0
        while True:
            params: dict[str, Any] = {
                "format": "json",
                "include": "data",
                "limit": limit,
                "start": start,
            }
            if since is not None:
                params["since"] = since
            response = await self._get(path, params)
            latest_version = max(latest_version, _header_version(response, latest_version))
            payload = response.json()
            page = payload if isinstance(payload, list) else []
            records.extend(record for record in page if isinstance(record, dict))
            if len(page) < limit:
                break
            start += limit
        return ZoteroPage(records=records, library_version=latest_version)

    async def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"Zotero-API-Version": "3"},
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ZoteroEndpointUnavailableError(
                    f"Zotero Local API endpoint unavailable: {exc.request.url}"
                ) from exc
            raise ZoteroConnectionError(f"Zotero Local API request failed: {exc}") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise ZoteroConnectionError(f"Zotero Local API request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()


def _header_version(response: httpx.Response, default: int = 0) -> int:
    try:
        return int(response.headers.get("Last-Modified-Version", default))
    except (TypeError, ValueError):
        return default
