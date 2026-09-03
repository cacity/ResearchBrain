from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from researchbrain.agent.deepseek import GenerationError
from researchbrain.agent.gateway import CancellationSignal

ToolHandler = Callable[[BaseModel], Awaitable[Any]]
ToolEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
ToolHook = Callable[[str, BaseModel], Awaitable[None]]


class LocalSearchArguments(BaseModel):
    library_id: str
    query: str
    limit: int


class OnlineSearchArguments(BaseModel):
    query: str
    limit: int
    sources: list[str] = []
    query_id: str = ""
    subquestion_id: str = ""


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    arguments: type[BaseModel]
    handler: ToolHandler
    readonly: bool = True
    parallel: bool = True


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    value: Any = None
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return not self.error


class ResearchToolRegistry:
    """Validated, observable tool execution with a hard call budget."""

    def __init__(
        self,
        *,
        signal: CancellationSignal,
        event_sink: ToolEventSink | None = None,
        max_calls: int = 30,
        before_call: ToolHook | None = None,
        after_call: ToolHook | None = None,
    ):
        self.signal = signal
        self.event_sink = event_sink
        self.max_calls = max_calls
        self.before_call = before_call
        self.after_call = after_call
        self.call_count = 0
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"research tool already registered: {tool.name}")
        if not tool.readonly:
            raise ValueError("write-capable tools require an explicit approval workflow")
        self._tools[tool.name] = tool

    async def execute_many(
        self,
        name: str,
        arguments: list[dict[str, Any]],
        *,
        parallel: bool = True,
    ) -> list[ToolResult]:
        tool = self._tools.get(name)
        if not tool:
            raise GenerationError("unknown_tool", f"Unknown research tool: {name}")
        calls = [(uuid4().hex[:12], tool.arguments.model_validate(value)) for value in arguments]
        if self.call_count + len(calls) > self.max_calls:
            raise GenerationError("tool_budget_exhausted", "Research tool-call budget was exhausted")
        self.call_count += len(calls)

        async def run(call_id: str, parsed: BaseModel) -> ToolResult:
            self.signal.raise_if_cancelled()
            if self.before_call:
                await self.before_call(name, parsed)
            await self._emit(
                "tool_execution_start",
                {"call_id": call_id, "tool": name, "arguments": parsed.model_dump()},
            )
            try:
                value = await tool.handler(parsed)
                if self.after_call:
                    await self.after_call(name, parsed)
                result = ToolResult(call_id=call_id, name=name, value=value)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - returned to the loop as a typed tool error
                result = ToolResult(call_id=call_id, name=name, error=str(exc))
            await self._emit(
                "tool_execution_end",
                {
                    "call_id": call_id,
                    "tool": name,
                    "status": "completed" if result.succeeded else "failed",
                    "error": result.error,
                },
            )
            return result

        if parallel and tool.parallel:
            return list(await asyncio.gather(*(run(call_id, parsed) for call_id, parsed in calls)))
        results: list[ToolResult] = []
        for call_id, parsed in calls:
            results.append(await run(call_id, parsed))
        return results

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink:
            await self.event_sink(event_type, payload)
