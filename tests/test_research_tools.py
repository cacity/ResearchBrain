import asyncio

import pytest
from pydantic import BaseModel

from researchbrain.agent.deepseek import GenerationError
from researchbrain.agent.gateway import CancellationSignal
from researchbrain.orchestration.tools import RegisteredTool, ResearchToolRegistry


class Arguments(BaseModel):
    value: int


@pytest.mark.asyncio
async def test_tool_registry_validates_streams_and_preserves_result_order():
    events: list[tuple[str, dict]] = []

    async def sink(kind, payload):
        events.append((kind, payload))

    async def execute(arguments: Arguments):
        await asyncio.sleep(0.002 if arguments.value == 1 else 0)
        return arguments.value * 2

    registry = ResearchToolRegistry(
        signal=CancellationSignal(),
        event_sink=sink,
        max_calls=2,
    )
    registry.register(RegisteredTool(name="double", arguments=Arguments, handler=execute))

    results = await registry.execute_many(
        "double",
        [{"value": 1}, {"value": 2}],
        parallel=True,
    )

    assert [value.value for value in results] == [2, 4]
    assert registry.call_count == 2
    assert [kind for kind, _ in events].count("tool_execution_start") == 2
    assert [kind for kind, _ in events].count("tool_execution_end") == 2


@pytest.mark.asyncio
async def test_tool_registry_enforces_budget_and_read_only_registration():
    async def execute(arguments: Arguments):
        return arguments.value

    registry = ResearchToolRegistry(signal=CancellationSignal(), max_calls=1)
    with pytest.raises(ValueError, match="approval workflow"):
        registry.register(
            RegisteredTool(
                name="write",
                arguments=Arguments,
                handler=execute,
                readonly=False,
            )
        )
    registry.register(RegisteredTool(name="read", arguments=Arguments, handler=execute))

    with pytest.raises(GenerationError) as caught:
        await registry.execute_many("read", [{"value": 1}, {"value": 2}])

    assert caught.value.code == "tool_budget_exhausted"
