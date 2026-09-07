from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from researchbrain.agent.deepseek import GenerationError
from researchbrain.agent.gateway import CancellationSignal
from researchbrain.orchestration.models import AgentAction, AgentToolCall, ToolResultMessage

ToolHandler = Callable[[BaseModel], Awaitable[Any]]
ToolEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
BeforeToolHook = Callable[[str, BaseModel, "RegisteredTool", str], Awaitable[None]]
AfterToolHook = Callable[[str, BaseModel, Any, "RegisteredTool", str, float], Awaitable[Any | None]]


class LocalSearchArguments(BaseModel):
    library_id: str
    query: str
    limit: int
    required_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    start_year: int | None = None
    end_year: int | None = None
    item_types: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    journals: list[str] = Field(default_factory=list)
    evidence_levels: list[str] = Field(default_factory=list)


class OnlineSearchArguments(BaseModel):
    query: str
    limit: int
    sources: list[str] = Field(default_factory=list)
    query_id: str = ""
    subquestion_id: str = ""
    start_year: int | None = None
    end_year: int | None = None
    track_citations: bool = True


class GetItemArguments(BaseModel):
    library_id: str
    item_id: str


class FulltextChunkReadArguments(BaseModel):
    library_id: str
    item_id: str
    query: str = ""
    section: str = ""
    page_start: int | None = None
    page_end: int | None = None
    target_kind: Literal["", "figure", "table", "equation", "references"] = ""
    limit: int = 4
    exclude_chunk_ids: list[str] = Field(default_factory=list)


class LookupDoiArguments(BaseModel):
    doi: str


class ImportDoisArguments(BaseModel):
    library_id: str
    dois: list[str] = Field(min_length=1, max_length=100)
    include_si: bool = False
    approval_id: str = ""
    idempotency_key: str = Field(min_length=8, max_length=500)


class QueueFulltextArguments(BaseModel):
    library_id: str
    item_id: str
    doi: str = ""
    include_si: bool = False
    approval_id: str = ""
    idempotency_key: str = Field(min_length=8, max_length=500)


class JobStatusArguments(BaseModel):
    library_id: str
    job_ids: list[str] = Field(min_length=1, max_length=100)


class ParsePdfArguments(BaseModel):
    library_id: str
    item_id: str
    attachment_id: str
    approval_id: str = ""
    idempotency_key: str = Field(min_length=8, max_length=500)


class EmbedDocumentArguments(BaseModel):
    library_id: str
    item_id: str
    attachment_id: str
    artifact_id: str
    approval_id: str = ""
    idempotency_key: str = Field(min_length=8, max_length=500)


class ExportReferencesArguments(BaseModel):
    library_id: str
    item_ids: list[str] = Field(min_length=1, max_length=500)
    format: Literal["csl-json", "bibtex", "ris", "doi", "markdown"] = "markdown"


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    arguments: type[BaseModel]
    handler: ToolHandler
    readonly: bool = True
    parallel: bool = True
    permission: Literal["read", "write"] = "read"
    timeout_seconds: float = 30.0
    idempotency: Literal["none", "arguments", "required_key"] = "arguments"
    approval_required: bool = False


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    value: Any = None
    error: str = ""
    error_code: str = ""
    cached: bool = False

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
        before_call: BeforeToolHook | None = None,
        after_call: AfterToolHook | None = None,
        steering_abort_event: asyncio.Event | None = None,
    ):
        self.signal = signal
        self.event_sink = event_sink
        self.max_calls = max_calls
        self.before_call = before_call
        self.after_call = after_call
        self.steering_abort_event = steering_abort_event
        self.call_count = 0
        self._budget_lock = asyncio.Lock()
        self._tools: dict[str, RegisteredTool] = {}
        self.context_messages: list[ToolResultMessage] = []
        self._cached_results: dict[str, Any] = {}

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "arguments_schema": tool.arguments.model_json_schema(),
                "permission": tool.permission,
                "readonly": tool.readonly,
                "parallel": tool.parallel,
                "timeout_seconds": tool.timeout_seconds,
                "idempotency": tool.idempotency,
                "approval_required": tool.approval_required,
            }
            for tool in self._tools.values()
        ]

    def preload_cached_results(self, results: dict[str, Any]) -> None:
        self._cached_results.update(results)

    def register(self, tool: RegisteredTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"research tool already registered: {tool.name}")
        if not tool.readonly and (not self.before_call or not tool.approval_required):
            raise ValueError("write-capable tools require an explicit approval workflow")
        if tool.readonly and tool.permission != "read":
            raise ValueError("readonly tools must use read permission")
        if not tool.readonly and tool.permission != "write":
            raise ValueError("write-capable tools must use write permission")
        self._tools[tool.name] = tool

    async def execute_many(
        self,
        name: str,
        arguments: list[dict[str, Any]],
        *,
        parallel: bool = True,
        action: AgentAction | None = None,
    ) -> list[ToolResult]:
        if not arguments:
            return []
        tool = self._tools.get(name)
        if action is not None:
            if action.action != "call_tools":
                raise ValueError("declared tool action must use call_tools")
            if len(action.tool_calls) != len(arguments):
                raise ValueError("declared tool action does not match the argument batch")
            if any(call.tool != name for call in action.tool_calls):
                raise ValueError("one tool action batch may only call the selected tool")
            call_ids = [call.id for call in action.tool_calls]
            if len(set(call_ids)) != len(call_ids):
                raise ValueError("tool call IDs must be unique within an action")
            if any(call.arguments != value for call, value in zip(action.tool_calls, arguments, strict=True)):
                raise ValueError("declared tool arguments do not match the execution batch")
            declared_action = action
            source = "model"
        else:
            call_ids = [uuid4().hex[:12] for _ in arguments]
            declared_action = AgentAction(
                action="call_tools",
                rationale=f"Execute the bounded {name} tool batch.",
                tool_calls=[
                    AgentToolCall(
                        id=call_id,
                        tool=name,
                        arguments=value,
                        permission=tool.permission if tool else "read",
                    )
                    for call_id, value in zip(call_ids, arguments, strict=True)
                ],
            )
            source = "tool_registry"
        await self._emit("agent_turn_started", {"source": source})
        await self._emit("agent_action", declared_action.model_dump())
        if not tool:
            results = []
            for call_id, raw_arguments in zip(call_ids, arguments, strict=True):
                result = ToolResult(
                    call_id=call_id,
                    name=name,
                    error=f"Unknown research tool: {name}",
                    error_code="unknown_tool",
                )
                await self._emit(
                    "tool_execution_start",
                    {"call_id": call_id, "tool": name, "arguments": raw_arguments},
                )
                await self._emit(
                    "tool_execution_end",
                    {
                        "call_id": call_id,
                        "tool": name,
                        "status": "failed",
                        "error": result.error,
                        "error_class": result.error_code,
                        "duration_ms": 0.0,
                        "cached": False,
                    },
                )
                await self._record_result(result)
                results.append(result)
            await self._emit("agent_turn_completed", {"status": "failed", "action": "call_tools"})
            return results
        async with self._budget_lock:
            budget_exhausted = self.call_count + len(arguments) > self.max_calls
            if not budget_exhausted:
                self.call_count += len(arguments)
        if budget_exhausted:
            await self._emit(
                "agent_turn_completed",
                {"status": "failed", "action": "call_tools", "error_code": "tool_budget_exhausted"},
            )
            raise GenerationError("tool_budget_exhausted", "Research tool-call budget was exhausted")
        calls: list[tuple[str, dict[str, Any], BaseModel | None, ToolResult | None]] = []
        for call_id, value in zip(call_ids, arguments, strict=True):
            try:
                calls.append((call_id, value, tool.arguments.model_validate(value), None))
            except ValidationError as exc:
                calls.append(
                    (
                        call_id,
                        value,
                        None,
                        ToolResult(
                            call_id=call_id,
                            name=name,
                            error=str(exc),
                            error_code="invalid_arguments",
                        ),
                    )
                )

        async def run(
            call_id: str,
            raw_arguments: dict[str, Any],
            parsed: BaseModel | None,
            invalid: ToolResult | None,
        ) -> ToolResult:
            if invalid:
                await self._emit(
                    "tool_execution_start",
                    {"call_id": call_id, "tool": name, "arguments": raw_arguments},
                )
                await self._emit(
                    "tool_execution_end",
                    {
                        "call_id": call_id,
                        "tool": name,
                        "status": "failed",
                        "error": invalid.error,
                        "error_class": invalid.error_code,
                        "duration_ms": 0.0,
                        "cached": False,
                    },
                )
                await self._record_result(invalid)
                return invalid
            assert parsed is not None
            self.signal.raise_if_cancelled()
            started_at = time.monotonic()
            await self._emit(
                "tool_execution_start",
                {"call_id": call_id, "tool": name, "arguments": parsed.model_dump()},
            )
            cache_key = tool_cache_key(name, parsed)
            try:
                if tool.readonly and cache_key in self._cached_results:
                    value = _restore_tool_value(name, self._cached_results[cache_key])
                    result = ToolResult(call_id=call_id, name=name, value=value, cached=True)
                else:
                    if self.before_call:
                        await self.before_call(name, parsed, tool, call_id)
                    value = await asyncio.wait_for(tool.handler(parsed), timeout=tool.timeout_seconds)
                    duration_ms = (time.monotonic() - started_at) * 1000
                    if self.after_call:
                        normalized = await self.after_call(name, parsed, value, tool, call_id, duration_ms)
                        if normalized is not None:
                            value = normalized
                    result = ToolResult(call_id=call_id, name=name, value=value)
                    if tool.readonly:
                        self._cached_results[cache_key] = _json_value(value)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                result = ToolResult(
                    call_id=call_id,
                    name=name,
                    error=f"tool timed out after {tool.timeout_seconds:g}s",
                    error_code="timeout",
                )
            except Exception as exc:  # noqa: BLE001 - returned to the loop as a typed tool error
                result = ToolResult(
                    call_id=call_id,
                    name=name,
                    error=str(exc),
                    error_code=_tool_error_class(str(exc)),
                )
            await self._emit(
                "tool_execution_end",
                {
                    "call_id": call_id,
                    "tool": name,
                    "status": "completed" if result.succeeded else "failed",
                    "error": result.error,
                    "error_class": result.error_code or _tool_error_class(result.error),
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                    "cached": result.cached,
                },
            )
            await self._record_result(result)
            return result

        async def run_interruptible(
            call_id: str,
            raw_arguments: dict[str, Any],
            parsed: BaseModel | None,
            invalid: ToolResult | None,
        ) -> ToolResult:
            abort_event = self.steering_abort_event
            if not abort_event:
                return await run(call_id, raw_arguments, parsed, invalid)
            if abort_event.is_set():
                await self._emit(
                    "tool_execution_start",
                    {"call_id": call_id, "tool": name, "arguments": raw_arguments},
                )
                return await self._record_interrupted_result(name, call_id, raw_arguments)
            run_task = asyncio.create_task(run(call_id, raw_arguments, parsed, invalid))
            abort_task = asyncio.create_task(abort_event.wait())
            done, pending = await asyncio.wait({run_task, abort_task}, return_when=asyncio.FIRST_COMPLETED)
            if abort_task in done and not run_task.done():
                run_task.cancel()
                with suppress(asyncio.CancelledError):
                    await run_task
                return await self._record_interrupted_result(name, call_id, raw_arguments)
            abort_task.cancel()
            for task in pending:
                task.cancel()
            return await run_task

        if parallel and tool.parallel:
            results = list(await asyncio.gather(*(run_interruptible(*call) for call in calls)))
        else:
            results = []
            for call in calls:
                results.append(await run_interruptible(*call))
        await self._emit(
            "agent_turn_completed",
            {
                "status": "completed" if all(value.succeeded for value in results) else "failed",
                "action": "call_tools",
            },
        )
        return results

    async def _record_interrupted_result(
        self,
        name: str,
        call_id: str,
        raw_arguments: dict[str, Any],
    ) -> ToolResult:
        result = ToolResult(
            call_id=call_id,
            name=name,
            error="tool interrupted by user steering constraint; replan at the next phase boundary",
            error_code="steering_interrupted",
        )
        await self._emit(
            "tool_execution_end",
            {
                "call_id": call_id,
                "tool": name,
                "status": "failed",
                "error": result.error,
                "error_class": result.error_code,
                "duration_ms": 0.0,
                "cached": False,
                "interrupted_by_steering": True,
                "replan_at_phase_boundary": True,
                "arguments": raw_arguments,
            },
        )
        await self._record_result(result)
        return result

    async def _record_result(self, result: ToolResult) -> None:
        message = ToolResultMessage(
            tool_call_id=result.call_id,
            tool=result.name,
            status="completed" if result.succeeded else "failed",
            result=_json_value(result.value) if result.succeeded else None,
            error=(
                None
                if result.succeeded
                else {"code": result.error_code or _tool_error_class(result.error), "message": result.error}
            ),
        )
        self.context_messages.append(message)
        await self._emit("tool_result", message.model_dump())

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink:
            await self.event_sink(event_type, payload)


def _restore_tool_value(name: str, value: Any) -> Any:
    if name in {"search_library", "read_fulltext_chunks"} and isinstance(value, list):
        from researchbrain.retrieval.index import SearchHit

        return (
            value[0],
            [SearchHit(**hit) for hit in value[1]],
            value[2],
        )
    if name == "search_online" and isinstance(value, list):
        from researchbrain.discovery.service import (
            DiscoveryMergeReport,
            DiscoveryRecord,
            DiscoverySearchResult,
            ProviderStatus,
        )

        result = value[1]
        restored = DiscoverySearchResult(
            records=[DiscoveryRecord(**record) for record in result.get("records", [])],
            providers=[ProviderStatus(**status) for status in result.get("providers", [])],
            merge_report=[DiscoveryMergeReport(**report) for report in result.get("merge_report", [])],
            ranking_report=result.get("ranking_report", []),
            seed_report=result.get("seed_report", []),
        )
        return value[0], restored
    return value


def tool_cache_key(name: str, arguments: BaseModel | dict[str, Any]) -> str:
    payload = arguments.model_dump(mode="json") if isinstance(arguments, BaseModel) else arguments
    import hashlib
    import json

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{name}:{serialized}".encode()).hexdigest()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    return str(value)


def _tool_error_class(error: str) -> str:
    if not error:
        return ""
    lowered = error.casefold()
    if "approval" in lowered:
        return "approval_required"
    if "library" in lowered or "scope" in lowered:
        return "scope_violation"
    if "budget" in lowered:
        return "budget_exhausted"
    if "domain" in lowered or "source" in lowered:
        return "domain_rejected"
    return "execution_error"
