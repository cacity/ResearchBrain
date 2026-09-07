import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select, update

from researchbrain.agent.deepseek import GenerationError
from researchbrain.agent.service import AgentAnswer, Evidence
from researchbrain.api.app import create_app
from researchbrain.db.base import Database
from researchbrain.db.models import (
    ChatSessionMemory,
    ImportBatch,
    ResearchEvidence,
    ResearchFollowUp,
    ResearchRun,
)
from researchbrain.orchestration.orchestrator import ResearchOrchestrator
from researchbrain.orchestration.tools import RegisteredTool


def wait_for_status(client: TestClient, run_id: str, expected: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.get(f"/v1/research/runs/{run_id}").json()
        if result["status"] == expected:
            return result
        time.sleep(0.03)
    raise AssertionError(f"run {run_id} did not reach {expected}")


def test_research_run_persists_progress_answer_and_events(settings, monkeypatch):
    async def fake_run(self, _library_id, _question, **_kwargs):
        await self._emit("phase_started", {"phase": "planning", "label": "正在拆分研究问题"})
        await self._emit("phase_completed", {"phase": "planning", "output": {"queries": ["q"]}})
        cited = Evidence(
            id="E1",
            chunk_id="chunk-1",
            item_id="item-1",
            title="Paper",
            text="Evidence",
            section="Results",
            page_start=2,
            page_end=2,
            score=0.9,
        )
        inspected = Evidence(
            id="E2",
            chunk_id="chunk-2",
            item_id="item-2",
            title="Background paper",
            text="Background evidence",
            section="Abstract",
            page_start=None,
            page_end=None,
            score=0.7,
        )
        return AgentAnswer(
            answer="A supported answer [E1].",
            evidence=[cited],
            citation_ids=["E1"],
            limitations=[],
            model="fixture",
            plan={"queries": ["q"]},
            coverage=[{"subquestion_id": "Q1", "status": "covered"}],
            metrics={"model_steps": 3},
            all_evidence=[cited, inspected],
        )

    monkeypatch.setattr(ResearchOrchestrator, "run", fake_run)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post("/v1/libraries", json={"name": "Research", "mode": "standalone"}).json()[
            "id"
        ]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "New research"}
        ).json()["id"]

        response = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Research this", "mode": "local", "evidence_limit": 10},
        )

        assert response.status_code == 202
        run_id = response.json()["id"]
        run = wait_for_status(client, run_id, "completed")
        assert run["assistant_message_id"]
        assert run["plan"] == {"queries": ["q"]}
        assert run["metrics"] == {"model_steps": 3}
        messages = client.get(f"/v1/chat/sessions/{session_id}/messages").json()
        assert [value["role"] for value in messages] == ["user", "assistant"]
        assert messages[-1]["citations"][0]["page_start"] == 2
        event_stream = client.get(f"/v1/research/runs/{run_id}/events")
        assert event_stream.status_code == 200
        assert "event: phase_started" in event_stream.text
        assert "event: run_completed" in event_stream.text
        listed = client.get(f"/v1/chat/sessions/{session_id}/runs").json()
        assert listed[0]["id"] == run_id

    database = Database(settings.database_url)
    with database.session() as session:
        evidence = list(
            session.scalars(
                select(ResearchEvidence)
                .where(ResearchEvidence.run_id == run_id)
                .order_by(ResearchEvidence.evidence_id)
            )
        )
        assert [value.evidence_id for value in evidence] == ["E1", "E2"]
        assert [value.cited for value in evidence] == [True, False]
        memory = session.get(ChatSessionMemory, session_id)
        assert memory is not None
        assert memory.summary["evidence_policy"] == "continuity_only_zero_evidentiary_weight"
        assert memory.summary["source_identifiers"] == ["item-1", "item-2"]
    database.engine.dispose()


def test_research_run_returns_visible_answer_when_no_evidence(settings, monkeypatch):
    async def no_evidence(*_args, **_kwargs):
        raise GenerationError("no_evidence", "none")

    monkeypatch.setattr(ResearchOrchestrator, "run", no_evidence)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post("/v1/libraries", json={"name": "Empty", "mode": "standalone"}).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "New research"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Unknown", "mode": "local"},
        ).json()["id"]

        wait_for_status(client, run_id, "completed")
        messages = client.get(f"/v1/chat/sessions/{session_id}/messages").json()
        assert "当前文库没有" in messages[-1]["content"]
        assert messages[-1]["model"] == "local-readiness-check"


def test_online_no_evidence_returns_search_diagnostics(settings, monkeypatch):
    async def no_evidence(self, *_args, **_kwargs):
        await self._emit(
            "query_diagnostic",
            {
                "query_id": "S1",
                "subquestion_id": "Q1",
                "language": "en",
                "source": "openalex",
                "query": "TEC earthquake ionosphere",
                "status": "failed",
                "error": "tool timed out after 45s",
                "retrieval_metrics": {},
            },
        )
        raise GenerationError("no_evidence", "none")

    monkeypatch.setattr(ResearchOrchestrator, "run", no_evidence)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post("/v1/libraries", json={"name": "Empty", "mode": "standalone"}).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "New research"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "TEC earthquake", "mode": "online"},
        ).json()["id"]

        wait_for_status(client, run_id, "completed")
        messages = client.get(f"/v1/chat/sessions/{session_id}/messages").json()
        assert "## 在线调研结果" in messages[-1]["content"]
        assert "TEC earthquake ionosphere" in messages[-1]["content"]
        assert "openalex" in messages[-1]["content"]
        assert "tool timed out after 45s" in messages[-1]["content"]


def test_research_run_returns_visible_chinese_request_when_clarification_times_out(settings, monkeypatch):
    async def clarification_required(self, *_args, **_kwargs):
        await self._emit(
            "ask_user",
            {
                "reason": "blocking_ambiguity",
                "questions": ["请确认要分析的具体研究主题。"],
                "resume_same_run": True,
            },
        )
        raise GenerationError("clarification_required", "需要先确认研究主题或范围，检索才能继续。")

    monkeypatch.setattr(ResearchOrchestrator, "run", clarification_required)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post("/v1/libraries", json={"name": "Clarify", "mode": "standalone"}).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "New research"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "分析这个主题", "mode": "local"},
        ).json()["id"]

        run = wait_for_status(client, run_id, "completed")
        messages = client.get(f"/v1/chat/sessions/{session_id}/messages").json()
        assert run["metrics"]["clarification_required"] is True
        assert "请确认要分析的具体研究主题" in messages[-1]["content"]
        assert "没有生成推测性结论" in messages[-1]["content"]
        assert messages[-1]["model"] == "local-clarification-request"


def test_active_research_run_can_be_cancelled(settings, monkeypatch):
    async def slow_run(*_args, **_kwargs):
        await asyncio.sleep(30)
        raise AssertionError("cancel did not stop the task")

    monkeypatch.setattr(ResearchOrchestrator, "run", slow_run)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post("/v1/libraries", json={"name": "Cancel", "mode": "standalone"}).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "New research"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Long research", "mode": "local"},
        ).json()["id"]

        response = client.post(f"/v1/research/runs/{run_id}/cancel")

        assert response.status_code == 200
        wait_for_status(client, run_id, "cancelled")


@pytest.mark.research_agent_action_loop
@pytest.mark.research_followup_recovery
def test_retry_resumes_from_safe_checkpoint_and_reuses_readonly_results(settings, monkeypatch):
    attempts = 0

    async def checkpoint_then_complete(self, _library_id, _question, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await self._emit("agent_turn_started", {"source": "tool_registry"})
            await self._emit(
                "agent_action",
                {
                    "action": "call_tools",
                    "rationale": "persist a readonly checkpoint",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "tool": "search_library",
                            "arguments": {"library_id": _library_id, "query": "cached", "limit": 5},
                            "permission": "read",
                        }
                    ],
                },
            )
            await self._emit(
                "tool_execution_start",
                {"call_id": "call-1", "tool": "search_library", "arguments": {"query": "cached"}},
            )
            await self._emit(
                "tool_result",
                {
                    "role": "tool_result",
                    "tool_call_id": "call-1",
                    "tool": "search_library",
                    "status": "completed",
                    "result": {"cached": True},
                    "error": None,
                },
            )
            await self._emit("agent_turn_completed", {"status": "completed", "action": "call_tools"})
            raise GenerationError("provider_unavailable", "crash after checkpoint")
        assert self.resume_checkpoint["turn_sequence"] == 1
        return AgentAnswer(
            answer="Recovered from checkpoint.",
            evidence=[],
            citation_ids=[],
            limitations=[],
            model="fixture",
        )

    monkeypatch.setattr(ResearchOrchestrator, "run", checkpoint_then_complete)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries", json={"name": "Checkpoint retry", "mode": "standalone"}
        ).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "Checkpoint"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Retry from checkpoint", "mode": "local"},
        ).json()["id"]
        wait_for_status(client, run_id, "failed")
        checkpoint = client.get(f"/v1/research/runs/{run_id}/checkpoint").json()
        assert checkpoint["turn_sequence"] == 1
        assert checkpoint["tool_results"][0]["status"] == "completed"

        retried = client.post(f"/v1/research/runs/{run_id}/retry")

        assert retried.status_code == 202
        wait_for_status(client, run_id, "completed")
        events = client.get(f"/v1/research/runs/{run_id}/events").text
        assert "event: checkpoint_resumed" in events
        assert '"readonly_results_reused":true' in events
        assert '"approval_valid":true' in events
        assert '"external_task_status"' in events
        turns = client.get(f"/v1/research/runs/{run_id}/turns").json()
        assert len([turn for turn in turns if turn["action"].get("action") == "call_tools"]) == 1


def test_failed_research_run_can_be_retried_without_duplicate_user_message(settings, monkeypatch):
    attempts = 0

    async def fail_then_complete(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise GenerationError("provider_unavailable", "temporary outage")
        return AgentAnswer(
            answer="Recovered answer.",
            evidence=[],
            citation_ids=[],
            limitations=[],
            model="fixture",
        )

    monkeypatch.setattr(ResearchOrchestrator, "run", fail_then_complete)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post("/v1/libraries", json={"name": "Retry", "mode": "standalone"}).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "New research"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Retry this", "mode": "local"},
        ).json()["id"]
        wait_for_status(client, run_id, "failed")

        retried = client.post(f"/v1/research/runs/{run_id}/retry")

        assert retried.status_code == 202
        wait_for_status(client, run_id, "completed")
        messages = client.get(f"/v1/chat/sessions/{session_id}/messages").json()
        assert [value["role"] for value in messages] == ["user", "assistant"]
        assert messages[-1]["content"] == "Recovered answer."


@pytest.mark.research_steering_abort
def test_running_steering_interrupts_cancellable_tool_and_replans_same_run(settings, monkeypatch):
    class SlowArguments(BaseModel):
        library_id: str

    async def slow_tool(_arguments):
        await asyncio.sleep(30)
        return "too late"

    async def interruptible_run(self, library_id, _question, **_kwargs):
        await self._enter("intake", "正在理解问题")
        await self._enter("planning", "正在拆分研究问题")
        await self._enter("local_search", "正在检索本地文库")
        self.tools.register(
            RegisteredTool(
                name="slow_cancellable_tool",
                arguments=SlowArguments,
                handler=slow_tool,
                timeout_seconds=60,
            )
        )
        results = await self.tools.execute_many(
            "slow_cancellable_tool",
            [{"library_id": library_id}],
            parallel=False,
        )
        await self._enter("evidence_inspection", "正在整理本地证据")
        assert results[0].error_code == "steering_interrupted"
        assert not self.signal.cancelled
        return AgentAnswer(
            answer="Replanned after steering.",
            evidence=[],
            citation_ids=[],
            limitations=[],
            model="test",
            plan={},
            coverage=[],
            metrics={"interrupted_by_steering": True},
        )

    monkeypatch.setattr(ResearchOrchestrator, "run", interruptible_run)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries", json={"name": "Steering interrupt", "mode": "standalone"}
        ).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "Steering interrupt"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Initial research", "mode": "local"},
        ).json()["id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if client.get(f"/v1/research/runs/{run_id}").json()["phase"] == "local_search":
                break
            time.sleep(0.03)

        response = client.post(
            f"/v1/research/runs/{run_id}/steer",
            json={"kind": "constraint", "content": "排除多波束声呐"},
        )

        assert response.status_code == 200
        completed = wait_for_status(client, run_id, "completed")
        assert completed["phase"] == "completed"
        replay = client.get(f"/v1/research/runs/{run_id}/events", headers={"Last-Event-ID": "1"}).text
        assert "event: steering_abort_requested" in replay
        assert '"current_tool_abort_signal":true' in replay
        assert '"error_class":"steering_interrupted"' in replay
        assert "event: steering_replan_requested" in replay
        assert '"interrupted_current_tool":true' in replay
        assert "event: run_cancelled" not in replay


@pytest.mark.research_steering_abort
def test_running_steering_emits_abort_request_and_sse_reconnect_replays_events(settings, monkeypatch):
    async def slow_run(self, _library_id, _question, **_kwargs):
        await self._emit("phase_started", {"phase": "local_search", "label": "正在检索本地文库"})
        while not self.signal.cancelled:
            await asyncio.sleep(0.05)
        raise asyncio.CancelledError

    monkeypatch.setattr(ResearchOrchestrator, "run", slow_run)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries", json={"name": "Steering abort", "mode": "standalone"}
        ).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "Steering"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Initial research", "mode": "local"},
        ).json()["id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if client.get(f"/v1/research/runs/{run_id}").json()["phase"] == "local_search":
                break
            time.sleep(0.03)

        response = client.post(
            f"/v1/research/runs/{run_id}/steer",
            json={"kind": "constraint", "content": "排除多波束声呐"},
        )

        assert response.status_code == 200
        client.post(f"/v1/research/runs/{run_id}/cancel")
        wait_for_status(client, run_id, "cancelled")
        cancelled_replay = client.get(
            f"/v1/research/runs/{run_id}/events", headers={"Last-Event-ID": "1"}
        ).text
        assert "event: steering_queued" in cancelled_replay
        assert "event: steering_abort_requested" in cancelled_replay
        assert '"replan_at_phase_boundary":true' in cancelled_replay
        assert '"current_phase":"local_search"' in cancelled_replay
        assert "event: run_cancelled" in cancelled_replay


@pytest.mark.research_followup_recovery
def test_follow_up_queue_is_distinct_from_steering_and_can_be_reordered_or_deleted(settings, monkeypatch):
    async def slow_run(*_args, **_kwargs):
        await asyncio.sleep(30)
        raise AssertionError("run should be cancelled by the test")

    monkeypatch.setattr(ResearchOrchestrator, "run", slow_run)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries", json={"name": "Follow-up queue", "mode": "standalone"}
        ).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "Queue"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Initial research", "mode": "local"},
        ).json()["id"]

        steering = client.post(
            f"/v1/research/runs/{run_id}/steer",
            json={"kind": "constraint", "content": "只看最近五年"},
        )
        first = client.post(
            f"/v1/research/runs/{run_id}/steer",
            json={"kind": "follow_up", "content": "比较两种方法"},
        ).json()["follow_up"]
        second = client.post(
            f"/v1/research/runs/{run_id}/steer",
            json={"kind": "follow_up", "content": "总结研究空白"},
        ).json()["follow_up"]

        assert steering.status_code == 200
        queue = client.get(f"/v1/chat/sessions/{session_id}/follow-ups").json()
        assert [value["content"] for value in queue] == ["比较两种方法", "总结研究空白"]
        reordered = client.put(
            f"/v1/chat/sessions/{session_id}/follow-ups/order",
            json={"ordered_ids": [second["id"], first["id"]]},
        ).json()
        assert [value["id"] for value in reordered] == [second["id"], first["id"]]
        deleted = client.delete(f"/v1/research/follow-ups/{first['id']}")
        assert deleted.status_code == 204
        remaining = client.get(f"/v1/chat/sessions/{session_id}/follow-ups").json()
        assert [value["id"] for value in remaining] == [second["id"]]
        client.post(f"/v1/research/runs/{run_id}/cancel")
        wait_for_status(client, run_id, "cancelled")
        events = client.get(f"/v1/research/runs/{run_id}/events").text
        assert "event: steering_queued" in events
        assert "event: follow_up_queued" in events

    database = Database(settings.database_url)
    with database.session() as session:
        persisted = session.get(ResearchFollowUp, second["id"])
        assert persisted is not None
        assert persisted.source_run_id == run_id
        assert persisted.source_message_id
        assert persisted.target_session_id == session_id
        assert persisted.position == 1
    database.engine.dispose()


@pytest.mark.research_followup_recovery
def test_completed_run_starts_queued_follow_ups_in_order(settings, monkeypatch):
    async def quick_run(self, _library_id, question, **_kwargs):
        if question == "Initial research":
            await asyncio.sleep(0.2)
        return AgentAnswer(
            answer=f"Completed: {question}",
            evidence=[],
            citation_ids=[],
            limitations=[],
            model="fixture",
        )

    monkeypatch.setattr(ResearchOrchestrator, "run", quick_run)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries", json={"name": "Follow-up execution", "mode": "standalone"}
        ).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "Queue"}
        ).json()["id"]
        initial_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Initial research", "mode": "local"},
        ).json()["id"]
        first = client.post(
            f"/v1/research/runs/{initial_id}/steer",
            json={"kind": "follow_up", "content": "First follow-up"},
        ).json()["follow_up"]
        second = client.post(
            f"/v1/research/runs/{initial_id}/steer",
            json={"kind": "follow_up", "content": "Second follow-up"},
        ).json()["follow_up"]

        wait_for_status(client, initial_id, "completed")
        deadline = time.monotonic() + 4
        runs = []
        while time.monotonic() < deadline:
            runs = client.get(f"/v1/chat/sessions/{session_id}/runs").json()
            if len(runs) == 3 and all(value["status"] == "completed" for value in runs):
                break
            time.sleep(0.03)
        assert len(runs) == 3
        messages = client.get(f"/v1/chat/sessions/{session_id}/messages").json()
        user_messages = [value["content"] for value in messages if value["role"] == "user"]
        assert user_messages == ["Initial research", "First follow-up", "Second follow-up"]
        history = client.get(f"/v1/chat/sessions/{session_id}/follow-ups?include_finished=true").json()
        by_id = {value["id"]: value for value in history}
        assert by_id[first["id"]]["status"] == "completed"
        assert by_id[second["id"]]["status"] == "completed"
        assert by_id[first["id"]]["started_run_id"]
        assert by_id[second["id"]]["started_run_id"]


@pytest.mark.research_doi_pdf_loop
def test_online_doi_acquisition_requires_one_time_approval(settings, monkeypatch):
    async def fake_run(self, _library_id, _question, **_kwargs):
        await self._emit(
            "approval_available",
            {
                "action": "import_dois",
                "dois": ["https://doi.org/10.1000/Open-Paper", "10.1000/open-paper"],
                "reason": "Open full text may be available",
            },
        )
        return AgentAnswer(
            answer="Online metadata exists [W1].",
            evidence=[
                Evidence(
                    id="W1",
                    chunk_id="web:crossref:10.1000/open-paper",
                    item_id="",
                    title="Online paper",
                    text="Title: Online paper\nDOI: 10.1000/open-paper",
                    section="online title/abstract",
                    page_start=None,
                    page_end=None,
                    score=1.0,
                    source_kind="online",
                    source_name="crossref",
                    source_url="https://doi.org/10.1000/open-paper",
                    discovery_record={"doi": "10.1000/open-paper", "abstract": ""},
                )
            ],
            citation_ids=["W1"],
            limitations=["Metadata only."],
            model="fixture",
        )

    monkeypatch.setattr(ResearchOrchestrator, "run", fake_run)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post("/v1/libraries", json={"name": "Approval", "mode": "standalone"}).json()[
            "id"
        ]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "New research"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Find online work", "mode": "online"},
        ).json()["id"]
        run = wait_for_status(client, run_id, "completed")
        approval = run["approvals"][0]

        approved = client.post(f"/v1/research/runs/{run_id}/approvals/{approval['id']}")

        assert approved.status_code == 202
        batch_id = approved.json()["batch_id"]
        assert batch_id
        assert approved.json()["approval"]["status"] == "approved"
        with Database(settings.database_url).session() as session:
            batch = session.get(ImportBatch, batch_id)
            assert batch.total == 1
        repeated = client.post(f"/v1/research/runs/{run_id}/approvals/{approval['id']}")
        assert repeated.status_code == 409
        turns = client.get(f"/v1/research/runs/{run_id}/turns").json()
        import_turn = next(value for value in turns if value["action"].get("action") == "call_tools")
        assert import_turn["tool_calls"][0]["tool"] == "import_dois"
        assert import_turn["tool_calls"][0]["readonly"] is False
        assert import_turn["tool_calls"][0]["status"] == "completed"
        checkpoint = client.get(f"/v1/research/runs/{run_id}/checkpoint").json()
        assert checkpoint["turn_sequence"] == import_turn["sequence"]


@pytest.mark.research_followup_recovery
@pytest.mark.research_doi_pdf_loop
def test_expired_research_approval_is_marked_and_rejected(settings, monkeypatch):
    async def fake_run(self, _library_id, _question, **_kwargs):
        await self._emit(
            "approval_available",
            {
                "action": "import_dois",
                "dois": ["10.1000/expired"],
                "reason": "Expired approval fixture",
            },
        )
        return AgentAnswer(
            answer="Needs approval.", evidence=[], citation_ids=[], limitations=[], model="fixture"
        )

    monkeypatch.setattr(ResearchOrchestrator, "run", fake_run)
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries", json={"name": "Expired approval", "mode": "standalone"}
        ).json()["id"]
        session_id = client.post(
            "/v1/chat/sessions", json={"library_id": library_id, "title": "Expired"}
        ).json()["id"]
        run_id = client.post(
            f"/v1/chat/sessions/{session_id}/runs",
            json={"content": "Find expired approval", "mode": "online"},
        ).json()["id"]
        run = wait_for_status(client, run_id, "completed")
        approval_id = run["approvals"][0]["id"]
        with Database(settings.database_url).session() as session:
            stored = session.get(ResearchRun, run_id)
            approvals = [dict(value) for value in stored.approvals]
            approvals[0]["expires_at"] = "2000-01-01T00:00:00+00:00"
            session.execute(update(ResearchRun).where(ResearchRun.id == run_id).values(approvals=approvals))

        response = client.post(f"/v1/research/runs/{run_id}/approvals/{approval_id}")

        assert response.status_code == 409
        assert response.json()["detail"] == "approval has expired"
        refreshed = client.get(f"/v1/research/runs/{run_id}").json()
        assert refreshed["approvals"][0]["status"] == "expired"
