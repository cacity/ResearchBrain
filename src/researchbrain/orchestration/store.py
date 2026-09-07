from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from researchbrain.agent.service import AgentAnswer
from researchbrain.db.base import Database
from researchbrain.db.models import (
    ChatMessage,
    ChatSession,
    ChatSessionMemory,
    ResearchCheckpoint,
    ResearchEvent,
    ResearchEvidence,
    ResearchFollowUp,
    ResearchRun,
    ResearchStep,
    ResearchToolCall,
    ResearchTurn,
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
                    "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(hours=24)).isoformat(),
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
            elif event_type == "plan_ready":
                run.plan = {
                    "research_intent": payload.get("research_intent") or {},
                    "subquestions": payload.get("subquestions") or [],
                    "queries": payload.get("queries") or [],
                    "query_specs": payload.get("query_specs") or [],
                    "topic_terms": payload.get("topic_terms") or [],
                    "excluded_terms": payload.get("excluded_terms") or [],
                }
            elif event_type == "coverage_updated":
                run.coverage = payload.get("coverage") or []
            elif event_type == "agent_turn_started":
                turn_sequence = (
                    int(
                        session.scalar(
                            select(func.max(ResearchTurn.sequence)).where(ResearchTurn.run_id == run_id)
                        )
                        or 0
                    )
                    + 1
                )
                turn = ResearchTurn(
                    run_id=run_id,
                    sequence=turn_sequence,
                    source=str(payload.get("source") or "agent"),
                    context=payload.get("context") or {},
                )
                session.add(turn)
                session.flush()
                event_payload["turn_id"] = turn.id
                event_payload["turn_sequence"] = turn_sequence
            elif event_type == "agent_action":
                turn = _latest_turn(session, run_id, running_only=True)
                if turn:
                    turn.action = dict(payload)
                    event_payload["turn_id"] = turn.id
                    event_payload["turn_sequence"] = turn.sequence
            elif event_type == "tool_execution_start":
                turn = _latest_turn(session, run_id, running_only=True)
                if turn:
                    arguments = payload.get("arguments") or {}
                    action_calls = {
                        str(value.get("id") or ""): value for value in (turn.action.get("tool_calls") or [])
                    }
                    declared = action_calls.get(str(payload.get("call_id") or ""), {})
                    readonly = not bool(declared.get("permission") == "write")
                    idempotency_key = str(arguments.get("idempotency_key") or "")
                    if not idempotency_key:
                        idempotency_key = _stored_tool_key(str(payload.get("tool") or ""), arguments)
                    tool_call = ResearchToolCall(
                        run_id=run_id,
                        turn_id=turn.id,
                        call_id=str(payload.get("call_id") or ""),
                        tool_name=str(payload.get("tool") or ""),
                        arguments=arguments,
                        readonly=readonly,
                        idempotency_key=idempotency_key,
                    )
                    session.add(tool_call)
                    event_payload["turn_id"] = turn.id
                    event_payload["turn_sequence"] = turn.sequence
            elif event_type == "tool_result":
                tool_call = session.scalar(
                    select(ResearchToolCall).where(
                        ResearchToolCall.run_id == run_id,
                        ResearchToolCall.call_id == str(payload.get("tool_call_id") or ""),
                    )
                )
                if tool_call:
                    tool_call.status = str(payload.get("status") or "failed")
                    tool_call.result = payload.get("result")
                    error = payload.get("error") or {}
                    tool_call.error_code = str(error.get("code") or "")
                    tool_call.error_message = str(error.get("message") or "")
                    tool_call.finished_at = now
                    event_payload["turn_id"] = tool_call.turn_id
            elif event_type == "agent_turn_completed":
                turn = _latest_turn(session, run_id, running_only=True)
                if turn:
                    turn.status = str(payload.get("status") or "completed")
                    turn.error_code = str(payload.get("error_code") or "")
                    turn.stop_decision = payload.get("stop_decision") or {}
                    turn.finished_at = now
                    calls = list(
                        session.scalars(
                            select(ResearchToolCall)
                            .where(ResearchToolCall.turn_id == turn.id)
                            .order_by(ResearchToolCall.started_at)
                        )
                    )
                    checkpoint_sequence = (
                        int(
                            session.scalar(
                                select(func.max(ResearchCheckpoint.sequence)).where(
                                    ResearchCheckpoint.run_id == run_id
                                )
                            )
                            or 0
                        )
                        + 1
                    )
                    checkpoint = ResearchCheckpoint(
                        run_id=run_id,
                        sequence=checkpoint_sequence,
                        turn_sequence=turn.sequence,
                        phase=run.phase,
                        action=turn.action,
                        tool_results=[_tool_call_dict(value) for value in calls],
                        coverage=payload.get("coverage") or run.coverage or [],
                        budgets=payload.get("budgets") or run.budgets or {},
                        context=payload.get("context") or turn.context or {},
                        safe=all(value.status in {"completed", "failed"} for value in calls),
                    )
                    session.add(checkpoint)
                    session.flush()
                    event_payload["turn_id"] = turn.id
                    event_payload["turn_sequence"] = turn.sequence
                    event_payload["checkpoint_id"] = checkpoint.id
                    event_payload["checkpoint_sequence"] = checkpoint_sequence
            elif event_type == "state_checkpoint":
                checkpoint_sequence = (
                    int(
                        session.scalar(
                            select(func.max(ResearchCheckpoint.sequence)).where(
                                ResearchCheckpoint.run_id == run_id
                            )
                        )
                        or 0
                    )
                    + 1
                )
                turn_sequence = int(
                    session.scalar(
                        select(func.max(ResearchTurn.sequence)).where(
                            ResearchTurn.run_id == run_id,
                            ResearchTurn.status != "running",
                        )
                    )
                    or 0
                )
                checkpoint = ResearchCheckpoint(
                    run_id=run_id,
                    sequence=checkpoint_sequence,
                    turn_sequence=turn_sequence,
                    phase=str(payload.get("phase") or run.phase),
                    action={"action": "resume_phase", "phase": payload.get("phase") or run.phase},
                    tool_results=[],
                    coverage=payload.get("coverage") or run.coverage or [],
                    budgets=payload.get("budgets") or run.budgets or {},
                    context=payload.get("context") or {},
                    safe=True,
                )
                session.add(checkpoint)
                session.flush()
                event_payload["checkpoint_id"] = checkpoint.id
                event_payload["checkpoint_sequence"] = checkpoint_sequence
                event_payload["turn_sequence"] = turn_sequence
            elif event_type == "stop_decision":
                turn = _latest_turn(session, run_id, running_only=False)
                if turn:
                    turn.stop_decision = dict(payload)
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

    def list_turns(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            turns = list(
                session.scalars(
                    select(ResearchTurn).where(ResearchTurn.run_id == run_id).order_by(ResearchTurn.sequence)
                )
            )
            return [_turn_dict(session, value) for value in turns]

    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            checkpoint = session.scalar(
                select(ResearchCheckpoint)
                .where(ResearchCheckpoint.run_id == run_id, ResearchCheckpoint.safe.is_(True))
                .order_by(ResearchCheckpoint.sequence.desc())
                .limit(1)
            )
            return _checkpoint_dict(checkpoint) if checkpoint else None

    def readonly_tool_cache(self, run_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            calls = list(
                session.scalars(
                    select(ResearchToolCall)
                    .where(
                        ResearchToolCall.run_id == run_id,
                        ResearchToolCall.readonly.is_(True),
                        ResearchToolCall.status == "completed",
                    )
                    .order_by(ResearchToolCall.finished_at)
                )
            )
            return {value.idempotency_key: value.result for value in calls if value.idempotency_key}

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
                parent_message_id=run.user_message_id,
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

    def begin_approval(self, run_id: str, approval_id: str) -> dict[str, Any]:
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
            expires_at = _parse_datetime(str(target.get("expires_at") or ""))
            if expires_at and expires_at <= datetime.now(UTC):
                target["status"] = "expired"
                run.approvals = approvals
                raise ValueError("approval has expired")
            target["status"] = "executing"
            target["authorized_at"] = datetime.now(UTC).isoformat()
            run.approvals = approvals
            run.updated_at = datetime.now(UTC)
            return target

    def expire_approval(self, run_id: str, approval_id: str) -> None:
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run:
                return
            approvals = [dict(value) for value in run.approvals]
            target = next((value for value in approvals if value.get("id") == approval_id), None)
            if target and target.get("status") == "pending":
                target["status"] = "expired"
                run.approvals = approvals
                run.updated_at = datetime.now(UTC)

    def release_approval(self, run_id: str, approval_id: str, error: str) -> None:
        with self.database.session() as session:
            run = session.get(ResearchRun, run_id)
            if not run:
                return
            approvals = [dict(value) for value in run.approvals]
            target = next((value for value in approvals if value.get("id") == approval_id), None)
            if target and target.get("status") == "executing":
                target["status"] = "pending"
                target["last_error"] = error[:1000]
                run.approvals = approvals
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
            if target.get("status") not in {"pending", "executing"}:
                raise ValueError("approval has already been handled")
            expires_at = _parse_datetime(str(target.get("expires_at") or ""))
            if expires_at and expires_at <= datetime.now(UTC):
                target["status"] = "expired"
                run.approvals = approvals
                raise ValueError("approval has expired")
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

    def enqueue_follow_up(
        self,
        source_run_id: str,
        content: str,
        *,
        source_message_id: str = "",
        target_session_id: str = "",
        target_branch_id: str = "",
    ) -> dict[str, Any]:
        with self.database.session() as session:
            run = session.get(ResearchRun, source_run_id)
            if not run:
                raise ValueError("research run not found")
            target_id = target_branch_id or target_session_id or run.session_id
            source_chat = session.get(ChatSession, run.session_id)
            target_chat = session.get(ChatSession, target_id)
            if not source_chat or not target_chat or source_chat.library_id != target_chat.library_id:
                raise ValueError("follow-up target must be a branch in the same library")
            position = (
                int(
                    session.scalar(
                        select(func.max(ResearchFollowUp.position)).where(
                            ResearchFollowUp.target_session_id == target_id,
                            ResearchFollowUp.status == "queued",
                        )
                    )
                    or 0
                )
                + 1
            )
            follow_up = ResearchFollowUp(
                source_run_id=source_run_id,
                source_message_id=source_message_id or run.user_message_id,
                target_session_id=target_id,
                target_branch_id=target_branch_id,
                position=position,
                content=content.strip(),
                mode=run.mode,
            )
            if not follow_up.content:
                raise ValueError("follow-up content is empty")
            session.add(follow_up)
            session.flush()
            return _follow_up_dict(follow_up)

    def list_follow_ups(self, session_id: str, *, include_finished: bool = False) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = select(ResearchFollowUp).where(ResearchFollowUp.target_session_id == session_id)
            if not include_finished:
                statement = statement.where(ResearchFollowUp.status.in_(["queued", "starting", "running"]))
            values = list(
                session.scalars(statement.order_by(ResearchFollowUp.position, ResearchFollowUp.created_at))
            )
            return [_follow_up_dict(value) for value in values]

    def reorder_follow_ups(self, session_id: str, ordered_ids: list[str]) -> list[dict[str, Any]]:
        with self.database.session() as session:
            queued = list(
                session.scalars(
                    select(ResearchFollowUp)
                    .where(
                        ResearchFollowUp.target_session_id == session_id,
                        ResearchFollowUp.status == "queued",
                    )
                    .order_by(ResearchFollowUp.position, ResearchFollowUp.created_at)
                )
            )
            by_id = {value.id: value for value in queued}
            if set(ordered_ids) != set(by_id) or len(ordered_ids) != len(by_id):
                raise ValueError("ordered_ids must contain every queued follow-up exactly once")
            for position, follow_up_id in enumerate(ordered_ids, 1):
                by_id[follow_up_id].position = position
                by_id[follow_up_id].updated_at = datetime.now(UTC)
            session.flush()
            return [_follow_up_dict(by_id[value]) for value in ordered_ids]

    def delete_follow_up(self, follow_up_id: str) -> None:
        with self.database.session() as session:
            follow_up = session.get(ResearchFollowUp, follow_up_id)
            if not follow_up:
                raise ValueError("follow-up not found")
            if follow_up.status != "queued":
                raise ValueError("only queued follow-ups can be deleted")
            target_id = follow_up.target_session_id
            session.delete(follow_up)
            session.flush()
            queued = list(
                session.scalars(
                    select(ResearchFollowUp)
                    .where(
                        ResearchFollowUp.target_session_id == target_id,
                        ResearchFollowUp.status == "queued",
                    )
                    .order_by(ResearchFollowUp.position, ResearchFollowUp.created_at)
                )
            )
            for position, value in enumerate(queued, 1):
                value.position = position

    def queued_follow_up_targets(self, source_run_id: str) -> list[str]:
        with self.database.session() as session:
            return list(
                dict.fromkeys(
                    session.scalars(
                        select(ResearchFollowUp.target_session_id)
                        .where(
                            ResearchFollowUp.source_run_id == source_run_id,
                            ResearchFollowUp.status == "queued",
                        )
                        .order_by(ResearchFollowUp.position)
                    )
                )
            )

    def claim_next_follow_up(self, session_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            active = session.scalar(
                select(ResearchRun.id).where(
                    ResearchRun.session_id == session_id,
                    ResearchRun.status.in_(["queued", "running", "cancelling"]),
                )
            )
            if active:
                return None
            follow_up = session.scalar(
                select(ResearchFollowUp)
                .where(
                    ResearchFollowUp.target_session_id == session_id,
                    ResearchFollowUp.status == "queued",
                )
                .order_by(ResearchFollowUp.position, ResearchFollowUp.created_at)
                .limit(1)
            )
            if not follow_up:
                return None
            follow_up.status = "starting"
            follow_up.updated_at = datetime.now(UTC)
            session.flush()
            return _follow_up_dict(follow_up)

    def mark_follow_up_running(self, follow_up_id: str, run_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            follow_up = session.get(ResearchFollowUp, follow_up_id)
            if not follow_up or follow_up.status != "starting":
                raise ValueError("follow-up is not ready to start")
            follow_up.status = "running"
            follow_up.started_run_id = run_id
            follow_up.updated_at = datetime.now(UTC)
            session.flush()
            return _follow_up_dict(follow_up)

    def finish_follow_up_for_run(self, run_id: str, status: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            follow_up = session.scalar(
                select(ResearchFollowUp).where(ResearchFollowUp.started_run_id == run_id).limit(1)
            )
            if not follow_up:
                return None
            follow_up.status = "completed" if status == "completed" else "failed"
            follow_up.finished_at = datetime.now(UTC)
            follow_up.updated_at = datetime.now(UTC)
            session.flush()
            return _follow_up_dict(follow_up)

    def release_follow_up(self, follow_up_id: str) -> None:
        with self.database.session() as session:
            follow_up = session.get(ResearchFollowUp, follow_up_id)
            if follow_up and follow_up.status == "starting":
                follow_up.status = "queued"
                follow_up.updated_at = datetime.now(UTC)

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


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _latest_turn(session, run_id: str, *, running_only: bool) -> ResearchTurn | None:
    statement = select(ResearchTurn).where(ResearchTurn.run_id == run_id)
    if running_only:
        statement = statement.where(ResearchTurn.status == "running")
    return session.scalar(statement.order_by(ResearchTurn.sequence.desc()).limit(1))


def _stored_tool_key(name: str, arguments: dict[str, Any]) -> str:
    import json

    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{name}:{serialized}".encode()).hexdigest()


def _tool_call_dict(value: ResearchToolCall) -> dict[str, Any]:
    return {
        "call_id": value.call_id,
        "tool": value.tool_name,
        "arguments": value.arguments,
        "result": value.result,
        "status": value.status,
        "readonly": value.readonly,
        "idempotency_key": value.idempotency_key,
        "error": (
            {"code": value.error_code, "message": value.error_message}
            if value.error_code or value.error_message
            else None
        ),
    }


def _turn_dict(session, value: ResearchTurn) -> dict[str, Any]:
    calls = list(
        session.scalars(
            select(ResearchToolCall)
            .where(ResearchToolCall.turn_id == value.id)
            .order_by(ResearchToolCall.started_at)
        )
    )
    return {
        "id": value.id,
        "run_id": value.run_id,
        "sequence": value.sequence,
        "source": value.source,
        "status": value.status,
        "action": value.action,
        "context": value.context,
        "stop_decision": value.stop_decision,
        "error_code": value.error_code,
        "tool_calls": [_tool_call_dict(call) for call in calls],
        "started_at": value.started_at.isoformat(),
        "finished_at": value.finished_at.isoformat() if value.finished_at else None,
    }


def _checkpoint_dict(value: ResearchCheckpoint) -> dict[str, Any]:
    return {
        "id": value.id,
        "run_id": value.run_id,
        "sequence": value.sequence,
        "turn_sequence": value.turn_sequence,
        "phase": value.phase,
        "action": value.action,
        "tool_results": value.tool_results,
        "coverage": value.coverage,
        "budgets": value.budgets,
        "context": value.context,
        "safe": value.safe,
        "created_at": value.created_at.isoformat(),
    }


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


def _follow_up_dict(value: ResearchFollowUp) -> dict[str, Any]:
    return {
        "id": value.id,
        "source_run_id": value.source_run_id,
        "source_message_id": value.source_message_id,
        "target_session_id": value.target_session_id,
        "target_branch_id": value.target_branch_id,
        "position": value.position,
        "content": value.content,
        "mode": value.mode,
        "status": value.status,
        "started_run_id": value.started_run_id,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "finished_at": value.finished_at.isoformat() if value.finished_at else None,
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
