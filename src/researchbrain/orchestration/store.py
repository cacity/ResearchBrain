from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select

from researchbrain.agent.service import AgentAnswer
from researchbrain.db.base import Database
from researchbrain.db.models import (
    ChatMessage,
    ChatSession,
    ChatSessionMemory,
    ResearchEvent,
    ResearchEvidence,
    ResearchRun,
    ResearchStep,
)

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


class ResearchRunStore:
    def __init__(self, database: Database):
        self.database = database

    def create(
        self,
        session_id: str,
        user_message_id: str,
        question: str,
        mode: str,
        budgets: dict[str, Any],
    ) -> ResearchRun:
        with self.database.session() as session:
            run = ResearchRun(
                session_id=session_id,
                user_message_id=user_message_id,
                question=question,
                mode=mode,
                budgets=budgets,
            )
            session.add(run)
            session.flush()
            session.expunge(run)
            return run

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            return _run_dict(run) if run else None

    def get_model(self, run_id: str) -> ResearchRun | None:
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if run:
                session.expunge(run)
            return run

    def list_for_session(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.session() as session:
            runs = list(
                session.scalars(
                    select(ResearchRun)
                    .where(ResearchRun.session_id == session_id)
                    .order_by(ResearchRun.created_at.desc())
                    .limit(max(1, min(limit, 100)))
                )
            )
            return [_run_dict(run) for run in runs]

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run:
                raise ValueError("research run not found")
            sequence = (
                int(
                    session.scalar(
                        select(func.max(ResearchEvent.sequence)).where(ResearchEvent.run_id == run_id)
                    )
                    or 0
                )
                + 1
            )
            event_payload = {**payload, "run_id": run_id, "sequence": sequence}
            if event_type == "approval_available":
                dois = list(dict.fromkeys(str(value) for value in payload.get("dois") or []))
                existing = next(
                    (
                        value
                        for value in run.approvals
                        if value.get("action") == payload.get("action")
                        and value.get("dois") == dois
                        and value.get("status") == "pending"
                    ),
                    None,
                )
                approval = existing or {
                    "id": str(uuid.uuid4()),
                    "action": str(payload.get("action") or ""),
                    "dois": dois,
                    "reason": str(payload.get("reason") or ""),
                    "status": "pending",
                }
                if not existing:
                    run.approvals = [*run.approvals, approval]
                event_payload["approval"] = approval
            session.add(
                ResearchEvent(
                    run_id=run_id,
                    sequence=sequence,
                    event_type=event_type,
                    payload=event_payload,
                )
            )
            if event_type == "phase_started":
                phase = str(payload.get("phase") or run.phase)
                run.phase = phase
                run.status = "running"
                run.updated_at = now
                step_sequence = (
                    int(
                        session.scalar(
                            select(func.max(ResearchStep.sequence)).where(ResearchStep.run_id == run_id)
                        )
                        or 0
                    )
                    + 1
                )
                attempt = (
                    int(
                        session.scalar(
                            select(func.count(ResearchStep.id)).where(
                                ResearchStep.run_id == run_id,
                                ResearchStep.phase == phase,
                            )
                        )
                        or 0
                    )
                    + 1
                )
                session.add(
                    ResearchStep(
                        run_id=run_id,
                        sequence=step_sequence,
                        phase=phase,
                        attempt=attempt,
                        status="running",
                    )
                )
            elif event_type == "phase_completed":
                phase = str(payload.get("phase") or run.phase)
                step = session.scalar(
                    select(ResearchStep)
                    .where(
                        ResearchStep.run_id == run_id,
                        ResearchStep.phase == phase,
                        ResearchStep.status == "running",
                    )
                    .order_by(ResearchStep.sequence.desc())
                    .limit(1)
                )
                if step:
                    step.status = "completed"
                    step.output = payload.get("output") or {}
                    step.finished_at = now
            session.flush()
            return {"type": event_type, **event_payload, "created_at": now.isoformat()}

    def events_after(self, run_id: str, sequence: int) -> list[dict[str, Any]]:
        with self.database.session() as session:
            events = list(
                session.scalars(
                    select(ResearchEvent)
                    .where(ResearchEvent.run_id == run_id, ResearchEvent.sequence > sequence)
                    .order_by(ResearchEvent.sequence)
                )
            )
            return [
                {
                    "type": event.event_type,
                    **event.payload,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ]

    def has_terminal_event(self, run_id: str) -> bool:
        with self.database.session() as session:
            return bool(
                session.scalar(
                    select(func.count(ResearchEvent.id)).where(
                        ResearchEvent.run_id == run_id,
                        ResearchEvent.event_type.in_(["run_completed", "run_failed", "run_cancelled"]),
                    )
                )
            )

    def complete(self, run_id: str, answer: AgentAnswer) -> ChatMessage:
        now = datetime.now(UTC)
        citations = [asdict(value) for value in answer.evidence]
        all_evidence = answer.all_evidence or answer.evidence
        cited_ids = set(answer.citation_ids)
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run:
                raise ValueError("research run not found")
            message = ChatMessage(
                session_id=run.session_id,
                role="assistant",
                content=answer.answer,
                citations=citations,
                model=answer.model,
            )
            session.add(message)
            session.flush()
            run.assistant_message_id = message.id
            run.status = "completed"
            run.phase = "completed"
            run.plan = answer.plan or {}
            run.coverage = answer.coverage or []
            run.limitations = answer.limitations
            run.metrics = answer.metrics or {}
            run.error_code = ""
            run.error_message = ""
            run.updated_at = now
            run.finished_at = now
            chat = session.get(ChatSession, run.session_id)
            if chat:
                chat.updated_at = now
            session.execute(delete(ResearchEvidence).where(ResearchEvidence.run_id == run_id))
            for evidence in all_evidence:
                session.add(
                    ResearchEvidence(
                        run_id=run_id,
                        evidence_id=evidence.id,
                        evidence_fingerprint=_evidence_fingerprint(evidence),
                        item_id=evidence.item_id,
                        chunk_id=evidence.chunk_id,
                        source_kind=evidence.source_kind,
                        source_name=evidence.source_name,
                        source_url=evidence.source_url,
                        evidence_level=_evidence_level(evidence),
                        title=evidence.title,
                        text=evidence.text,
                        section=evidence.section,
                        page_start=evidence.page_start,
                        page_end=evidence.page_end,
                        score=evidence.score,
                        discovery_record=evidence.discovery_record or {},
                        selected=evidence.relevance in {"relevant", "unreviewed"},
                        cited=evidence.id in cited_ids,
                    )
                )
            self._save_memory(session, run, answer, message.id)
            session.flush()
            session.expunge(message)
            return message

    def fail(self, run_id: str, code: str, message: str) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run:
                return
            run.status = "failed"
            run.phase = "failed"
            run.error_code = code
            run.error_message = message
            run.updated_at = now
            run.finished_at = now
            step = session.scalar(
                select(ResearchStep)
                .where(ResearchStep.run_id == run_id, ResearchStep.status == "running")
                .order_by(ResearchStep.sequence.desc())
                .limit(1)
            )
            if step:
                step.status = "failed"
                step.error_code = code
                step.error_message = message
                step.finished_at = now

    def cancel(self, run_id: str) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run or run.status in TERMINAL_RUN_STATUSES:
                return
            run.status = "cancelled"
            run.phase = "cancelled"
            run.updated_at = now
            run.finished_at = now
            steps = list(
                session.scalars(
                    select(ResearchStep).where(
                        ResearchStep.run_id == run_id, ResearchStep.status == "running"
                    )
                )
            )
            for step in steps:
                step.status = "cancelled"
                step.finished_at = now

    def pause(self, run_id: str, message: str) -> None:
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run or run.status in TERMINAL_RUN_STATUSES:
                return
            run.status = "paused"
            run.phase = "paused"
            run.error_code = "application_restarted"
            run.error_message = message
            run.updated_at = datetime.now(UTC)

    def mark_stale_runs_paused(self) -> int:
        with self.database.session() as session:
            runs = list(
                session.scalars(
                    select(ResearchRun).where(ResearchRun.status.in_(["queued", "running", "cancelling"]))
                )
            )
            for run in runs:
                run.status = "paused"
                run.phase = "paused"
                run.error_code = "application_restarted"
                run.error_message = "The previous application process ended before this run completed"
            return len(runs)

    def reset_for_retry(self, run_id: str) -> None:
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run:
                raise ValueError("research run not found")
            if run.status not in {"failed", "paused", "cancelled"}:
                raise ValueError("only failed, paused, or cancelled runs can be retried")
            run.status = "queued"
            run.phase = "queued"
            run.error_code = ""
            run.error_message = ""
            run.finished_at = None
            run.updated_at = datetime.now(UTC)

    def approve(self, run_id: str, approval_id: str, batch_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run:
                raise ValueError("research run not found")
            approvals = [dict(value) for value in run.approvals]
            target = next((value for value in approvals if value.get("id") == approval_id), None)
            if not target:
                raise ValueError("approval not found")
            if target.get("status") != "pending":
                raise ValueError("approval has already been handled")
            target["status"] = "approved"
            target["batch_id"] = batch_id
            target["approved_at"] = datetime.now(UTC).isoformat()
            run.approvals = approvals
            run.updated_at = datetime.now(UTC)
            return target

    def reject(self, run_id: str, approval_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run:
                raise ValueError("research run not found")
            approvals = [dict(value) for value in run.approvals]
            target = next((value for value in approvals if value.get("id") == approval_id), None)
            if not target:
                raise ValueError("approval not found")
            if target.get("status") != "pending":
                raise ValueError("approval has already been handled")
            target["status"] = "rejected"
            target["rejected_at"] = datetime.now(UTC).isoformat()
            run.approvals = approvals
            run.updated_at = datetime.now(UTC)
            return target

    def load_memory(self, session_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            memory = session.get(ChatSessionMemory, session_id)
            return memory.summary if memory else {}

    @staticmethod
    def _save_memory(session, run: ResearchRun, answer: AgentAnswer, message_id: str) -> None:
        message_count = int(
            session.scalar(select(func.count(ChatMessage.id)).where(ChatMessage.session_id == run.session_id))
            or 0
        )
        unresolved = [
            str(value.get("question") or "")
            for value in (answer.coverage or [])
            if value.get("status") != "covered"
        ]
        identifiers = []
        for evidence in answer.all_evidence or answer.evidence:
            record = evidence.discovery_record or {}
            doi = str(record.get("doi") or "").strip()
            identifiers.append(doi or evidence.item_id or evidence.chunk_id)
        memory = session.get(ChatSessionMemory, run.session_id)
        previous = memory.summary if memory else {}
        prior_findings = [str(value) for value in previous.get("supported_findings") or []]
        prior_identifiers = [str(value) for value in previous.get("source_identifiers") or []]
        summary = {
            "goal": run.question,
            "constraints": list(previous.get("constraints") or []),
            "terminology": list(previous.get("terminology") or []),
            "supported_findings": [*prior_findings, answer.answer[:2000]][-5:],
            "source_identifiers": list(
                dict.fromkeys([*prior_identifiers, *(value for value in identifiers if value)])
            )[-50:],
            "unresolved_questions": [value for value in unresolved if value],
            "evidence_policy": "continuity_only_zero_evidentiary_weight",
        }
        if not memory:
            memory = ChatSessionMemory(session_id=run.session_id)
            session.add(memory)
        memory.summary = summary
        memory.through_message_id = message_id
        memory.message_count = message_count
        memory.updated_at = datetime.now(UTC)


def _run_dict(run: ResearchRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "session_id": run.session_id,
        "user_message_id": run.user_message_id,
        "assistant_message_id": run.assistant_message_id,
        "mode": run.mode,
        "status": run.status,
        "phase": run.phase,
        "question": run.question,
        "plan": run.plan,
        "coverage": run.coverage,
        "budgets": run.budgets,
        "approvals": run.approvals,
        "limitations": run.limitations,
        "metrics": run.metrics,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _evidence_level(evidence) -> str:
    if evidence.source_kind == "online":
        record = evidence.discovery_record or {}
        return "structured_abstract" if record.get("abstract") else "metadata"
    if evidence.chunk_id.startswith("metadata:") or evidence.section == "题录与摘要":
        return "structured_abstract" if "Abstract:" in evidence.text else "metadata"
    return "fulltext_page" if evidence.page_start is not None else "fulltext_section"


def _evidence_fingerprint(evidence) -> str:
    if evidence.chunk_id:
        return hashlib.sha256(evidence.chunk_id.encode()).hexdigest()
    payload = f"{evidence.source_kind}\n{evidence.title}\n{evidence.text}"
    return hashlib.sha256(payload.encode()).hexdigest()
