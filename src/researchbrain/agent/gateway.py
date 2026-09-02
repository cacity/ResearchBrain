from __future__ import annotations

import asyncio
import json
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from researchbrain.agent.deepseek import DeepSeekClient, GenerationError

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class CancellationSignal:
    def __init__(self, event: asyncio.Event | None = None):
        self._event = event or asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError


class ModelGateway(Protocol):
    model: str

    async def generate_structured(
        self,
        role: str,
        system: str,
        user: str,
        schema: type[SchemaT],
        signal: CancellationSignal,
    ) -> SchemaT: ...


class DeepSeekGateway:
    def __init__(self, client: DeepSeekClient):
        self.client = client
        self.model = client.model

    async def generate_structured(
        self,
        role: str,
        system: str,
        user: str,
        schema: type[SchemaT],
        signal: CancellationSignal,
    ) -> SchemaT:
        signal.raise_if_cancelled()
        schema_json = json.dumps(
            schema.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        structured_system = (
            f"{system}\n\n"
            "Your entire response must be one JSON object that validates against this exact JSON Schema. "
            "Use the exact property names, enum values, array shapes, and ID formats. "
            "Do not add commentary.\n"
            f"JSON Schema:\n{schema_json}"
        )
        validation_error = ""
        for attempt in range(2):
            prompt = user
            if validation_error:
                prompt = (
                    f"{user}\n\nYour previous JSON failed schema validation. Correct it without commentary.\n"
                    f"Validation error: {validation_error}"
                )
            payload = await self._generate_with_retry(structured_system, prompt, signal)
            signal.raise_if_cancelled()
            try:
                return schema.model_validate(payload)
            except ValidationError as exc:
                validation_error = str(exc)[:2000]
                if attempt == 1:
                    raise GenerationError(
                        "schema_validation_failed",
                        f"{role} returned invalid structured output: {validation_error}",
                    ) from exc
        raise AssertionError("unreachable")

    async def _generate_with_retry(
        self,
        system: str,
        prompt: str,
        signal: CancellationSignal,
    ) -> dict:
        for attempt in range(3):
            signal.raise_if_cancelled()
            try:
                return await self.client.generate_json(system, prompt)
            except GenerationError as exc:
                if exc.code not in {"timeout", "rate_limited", "provider_unavailable"} or attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (2**attempt))
        raise AssertionError("unreachable")
