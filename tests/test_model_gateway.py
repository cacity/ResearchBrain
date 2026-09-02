import asyncio

import pytest
from pydantic import BaseModel

from researchbrain.agent.deepseek import GenerationError
from researchbrain.agent.gateway import CancellationSignal, DeepSeekGateway


class ResultSchema(BaseModel):
    answer: str


class FixtureClient:
    model = "fixture"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def generate_json(self, _system, prompt):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_gateway_repairs_schema_once():
    client = FixtureClient([{"wrong": "field"}, {"answer": "valid"}])

    result = await DeepSeekGateway(client).generate_structured(
        "planner",
        "system",
        "request",
        ResultSchema,
        CancellationSignal(),
    )

    assert result.answer == "valid"
    assert "failed schema validation" in client.prompts[1]


@pytest.mark.asyncio
async def test_gateway_retries_transient_provider_errors(monkeypatch):
    client = FixtureClient([GenerationError("rate_limited", "slow down"), {"answer": "recovered"}])

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    result = await DeepSeekGateway(client).generate_structured(
        "assessor",
        "system",
        "request",
        ResultSchema,
        CancellationSignal(),
    )

    assert result.answer == "recovered"
    assert len(client.prompts) == 2


@pytest.mark.asyncio
async def test_gateway_honors_cancellation_before_calling_provider():
    client = FixtureClient([{"answer": "unused"}])
    signal = CancellationSignal()
    signal.cancel()

    with pytest.raises(asyncio.CancelledError):
        await DeepSeekGateway(client).generate_structured(
            "reviewer", "system", "request", ResultSchema, signal
        )

    assert client.prompts == []
