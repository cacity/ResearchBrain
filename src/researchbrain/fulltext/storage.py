from __future__ import annotations

import hashlib
import ipaddress
import os
import uuid
from collections.abc import AsyncIterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from researchbrain.fulltext.discovery import FullTextCandidate


class DownloadError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StoredObject:
    sha256: str
    path: Path
    bytes: int
    mime: str


class ObjectStore:
    def __init__(
        self,
        data_dir: Path,
        max_download_mb: int = 200,
        client: httpx.AsyncClient | None = None,
    ):
        self.data_dir = data_dir
        self.max_bytes = max_download_mb * 1024 * 1024
        self.staging_dir = data_dir / "cache" / "http"
        self.objects_dir = data_dir / "library" / "objects"
        self._client = client

    async def download_pdf(
        self,
        candidate: FullTextCandidate,
        client: httpx.AsyncClient | None = None,
    ) -> StoredObject:
        validate_remote_url(candidate.url)
        owns_client = client is None and self._client is None
        resolved_client = client or self._client or httpx.AsyncClient(timeout=90.0, follow_redirects=True)
        try:
            async with resolved_client.stream("GET", candidate.url) as response:
                response.raise_for_status()
                validate_remote_url(str(response.url))
                declared = response.headers.get("content-length")
                if declared and int(declared) > self.max_bytes:
                    raise DownloadError("too_large", f"PDF exceeds {self.max_bytes} bytes")
                return await self.store_pdf_stream(response.aiter_bytes(1024 * 128))
        except httpx.TimeoutException as exc:
            raise DownloadError("timeout", f"Download timed out: {candidate.url}") from exc
        except httpx.HTTPStatusError as exc:
            raise DownloadError("http_error", f"Download HTTP {exc.response.status_code}") from exc
        except (OSError, ValueError) as exc:
            raise DownloadError("download_error", str(exc)) from exc
        finally:
            if owns_client:
                await resolved_client.aclose()

    async def store_pdf_stream(self, chunks: AsyncIterable[bytes]) -> StoredObject:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        partial = self.staging_dir / f"{uuid.uuid4()}.partial"
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        try:
            with partial.open("xb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise DownloadError("too_large", f"PDF exceeds {self.max_bytes} bytes")
                    if len(prefix) < 16:
                        prefix.extend(chunk[: 16 - len(prefix)])
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if b"%PDF-" not in bytes(prefix):
                raise DownloadError("not_pdf", "Downloaded content does not have a PDF signature")
            sha256 = digest.hexdigest()
            destination = self.objects_dir / sha256[:2] / sha256[2:4] / f"{sha256}.pdf"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                partial.unlink(missing_ok=True)
            else:
                partial.replace(destination)
            return StoredObject(sha256=sha256, path=destination, bytes=size, mime="application/pdf")
        except DownloadError:
            raise
        except OSError as exc:
            raise DownloadError("storage_error", str(exc)) from exc
        finally:
            partial.unlink(missing_ok=True)


def validate_remote_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DownloadError("unsafe_url", "Full-text URL must use HTTP or HTTPS")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise DownloadError("unsafe_url", "Full-text URL cannot target a local or private IP address")
