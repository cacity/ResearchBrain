from __future__ import annotations

import json
from typing import Any

import httpx


class GenerationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client

    async def generate_json(self, system: str, user: str) -> dict[str, Any]:
        if not self.api_key:
            raise GenerationError("api_key_missing", "DeepSeek API key is not configured")
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=180.0)
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                    "stream": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            result = json.loads(content)
            if not isinstance(result, dict):
                raise GenerationError("invalid_response", "DeepSeek JSON output is not an object")
            return result
        except httpx.TimeoutException as exc:
            raise GenerationError("timeout", "DeepSeek request timed out") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                code = "authentication_failed"
            elif exc.response.status_code == 429:
                code = "rate_limited"
            elif exc.response.status_code >= 500:
                code = "provider_unavailable"
            else:
                code = "http_error"
            raise GenerationError(code, f"DeepSeek HTTP {exc.response.status_code}") from exc
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GenerationError("invalid_response", f"Invalid DeepSeek response: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()
