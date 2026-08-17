from __future__ import annotations

from typing import Literal

import httpx


class EmbeddingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MiniMaxEmbedder:
    provider = "minimax"

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://api.minimax.chat/v1/embeddings",
        group_id: str = "",
        model: str = "embo-01",
        dimensions: int = 1536,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.group_id = group_id
        self.model = model
        self.dimensions = dimensions
        self._client = client

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "db")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], "query")
        return vectors[0]

    async def _embed(self, texts: list[str], embedding_type: Literal["db", "query"]) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingError("api_key_missing", "MiniMax API key is not configured")
        if not texts:
            return []
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=90.0)
        params = {"GroupId": self.group_id} if self.group_id else None
        try:
            response = await client.post(
                self.endpoint,
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"texts": texts, "model": self.model, "type": embedding_type},
            )
            response.raise_for_status()
            payload = response.json()
            base_response = payload.get("base_resp") or {}
            status_code = int(base_response.get("status_code") or 0)
            if status_code != 0:
                raise EmbeddingError(
                    "provider_error",
                    str(base_response.get("status_msg") or f"MiniMax status {status_code}"),
                )
            vectors = payload.get("vectors")
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise EmbeddingError("invalid_response", "MiniMax returned an unexpected vector count")
            normalized = [[float(value) for value in vector] for vector in vectors]
            if any(len(vector) != self.dimensions for vector in normalized):
                raise EmbeddingError(
                    "dimension_mismatch",
                    f"MiniMax vector dimension does not match configured {self.dimensions}",
                )
            return normalized
        except httpx.TimeoutException as exc:
            raise EmbeddingError("timeout", "MiniMax embedding request timed out") from exc
        except httpx.HTTPStatusError as exc:
            code = "authentication_failed" if exc.response.status_code in {401, 403} else "http_error"
            raise EmbeddingError(code, f"MiniMax HTTP {exc.response.status_code}") from exc
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("invalid_response", f"Invalid MiniMax embedding response: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()
