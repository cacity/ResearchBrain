import asyncio
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from researchbrain.agent.deepseek import GenerationError
from researchbrain.agent.service import AgentAnswer, Evidence
from researchbrain.api.app import create_app
from researchbrain.db.base import Database
from researchbrain.db.models import ChatSessionMemory, ResearchEvidence
from researchbrain.orchestration.orchestrator import ResearchOrchestrator


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


def test_online_doi_acquisition_requires_one_time_approval(settings, monkeypatch):
    async def fake_run(self, _library_id, _question, **_kwargs):
        await self._emit(
            "approval_available",
            {
                "action": "import_dois",
                "dois": ["10.1000/open-paper"],
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
        assert approved.json()["batch_id"]
        assert approved.json()["approval"]["status"] == "approved"
        repeated = client.post(f"/v1/research/runs/{run_id}/approvals/{approval['id']}")
        assert repeated.status_code == 409
