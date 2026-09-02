from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from researchbrain.agent.deepseek import GenerationError
from researchbrain.agent.gateway import CancellationSignal, ModelGateway
from researchbrain.agent.service import (
    SYSTEM_PROMPT,
    AgentAnswer,
    ConversationTurn,
    Evidence,
    ResearchMode,
    _build_prompt,
)
from researchbrain.discovery.service import LiteratureDiscovery, ProviderStatus
from researchbrain.orchestration.evidence import EvidenceLedger
from researchbrain.orchestration.models import (
    CoverageItem,
    DraftAnswer,
    GapAssessment,
    ResearchBudgets,
    ResearchPlan,
    ResearchSubquestion,
    ReviewResult,
    ScoutFinding,
)
from researchbrain.orchestration.state_machine import ResearchStateMachine
from researchbrain.retrieval.service import EmbeddingPipeline

EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
SteeringSource = Callable[[], Awaitable[list[dict[str, str]]]]
AcquisitionSource = Callable[[], Awaitable[dict[str, Any] | None]]

PLANNER_PROMPT = """You are the planning stage of an evidence-grounded literature research system.
Decompose the task into answerable subquestions and produce concise multilingual academic queries.
Require full-text evidence for detailed methods, numerical results, figures, tables, equations, and pages.
Use structured_abstract for broad landscape questions and metadata only for bibliographic existence.
Do not answer the research question. Return JSON matching the requested schema."""

ASSESSOR_PROMPT = """You are the evidence coverage assessor in a literature research system.
For each supplied subquestion, decide whether the evidence is covered, partial, or insufficient_evidence.
Only use listed evidence IDs. Respect evidence levels. Suggest focused missing queries when useful.
Choose local_search only when a new local query can plausibly close a gap; choose online_search when
external coverage is required; otherwise choose synthesize. Do not write the final answer.
Return JSON only."""

REVIEWER_PROMPT = """You are an independent reviewer of an evidence-grounded literature answer.
Find unsupported factual claims, invalid citations, evidence-level violations, contradictions, and unanswered
subquestions. Metadata proves only bibliographic facts. Abstracts do not prove figure, page, equation, exact
parameter, or detailed workflow claims. Return structured JSON only. Do not rewrite the answer."""

REVISION_PROMPT = """Revise the draft using only the supplied evidence and reviewer issues.
Remove unsupported details, answer missing subquestions when evidence permits, and keep evidence IDs next to
the claims they support. Do not create sources or facts.
Return JSON with answer, citation_ids, limitations."""

SCOUT_PROMPT = """You are a read-only evidence scout assigned one literature subquestion.
Extract only findings directly supported by the supplied evidence. List the exact evidence IDs used, identify
missing information, and suggest focused queries. Respect evidence levels and do not write a final answer.
Return structured JSON only."""

_CITATION_RE = re.compile(r"\[([ELW]\d+)\]")


class ResearchOrchestrator:
    def __init__(
        self,
        retrieval: EmbeddingPipeline,
        gateway: ModelGateway,
        discovery: LiteratureDiscovery | None = None,
        *,
        budgets: ResearchBudgets | None = None,
        event_sink: EventSink | None = None,
        signal: CancellationSignal | None = None,
        steering_source: SteeringSource | None = None,
        acquisition_source: AcquisitionSource | None = None,
    ):
        self.retrieval = retrieval
        self.gateway = gateway
        self.discovery = discovery
        self.budgets = budgets or ResearchBudgets()
        self.event_sink = event_sink
        self.signal = signal or CancellationSignal()
        self.steering_source = steering_source
        self.acquisition_source = acquisition_source
        self.state = ResearchStateMachine()
        self.model_steps = 0
        self.tool_calls = 0
        self.started_at = 0.0
        self.limitations: list[str] = []
        self.steering: list[dict[str, str]] = []
        self.scout_findings: list[ScoutFinding] = []
        self.scout_rounds = 0

    async def run(
        self,
        library_id: str,
        question: str,
        *,
        mode: ResearchMode = "local",
        conversation_history: list[ConversationTurn] | None = None,
        evidence_limit: int = 20,
        session_memory: dict[str, Any] | None = None,
    ) -> AgentAnswer:
        if mode not in {"local", "hybrid", "online"}:
            raise ValueError(f"unsupported research mode: {mode}")
        self.started_at = time.monotonic()
        history = (conversation_history or [])[-8:]
        ledger = EvidenceLedger("E" if mode == "local" else "L")
        statuses: list[ProviderStatus] = []

        await self._enter("intake", "正在理解问题")
        self.signal.raise_if_cancelled()
        await self._complete("intake", {"mode": mode, "history_messages": len(history)})

        await self._enter("planning", "正在拆分研究问题")
        plan = await self._plan(question, history, session_memory or {})
        await self._emit(
            "plan_ready",
            {
                "subquestions": [value.model_dump() for value in plan.subquestions],
                "queries": plan.queries,
            },
        )
        await self._complete("planning", {"subquestions": len(plan.subquestions), "queries": plan.queries})

        query_queue = _unique_queries([question, *plan.queries], self.budgets.max_queries)
        local_rounds = 0
        coverage: list[CoverageItem] = []

        if mode != "online":
            while query_queue and local_rounds < self.budgets.max_local_rounds:
                await self._enter("local_search", "正在检索本地文库")
                local_rounds += 1
                round_queries = query_queue[: self.budgets.max_queries]
                query_queue = []
                await self._search_local(library_id, round_queries, ledger)
                await self._complete(
                    "local_search",
                    {"round": local_rounds, "queries": round_queries, "evidence": len(ledger.entries())},
                )

                await self._enter("evidence_inspection", "正在整理本地证据")
                await self._emit_evidence_summary(ledger)
                await self._run_scouts(plan, ledger)
                await self._complete("evidence_inspection", {"evidence": len(ledger.entries())})

                await self._enter("gap_assessment", "正在检查证据缺口")
                assessment = await self._assess(plan, ledger, mode, allow_local=True)
                coverage = assessment.coverage
                await self._emit_coverage(coverage)
                await self._complete(
                    "gap_assessment",
                    {"next_action": assessment.next_action, "rationale": assessment.rationale},
                )
                if assessment.next_action != "local_search":
                    break
                query_queue = _unique_queries(assessment.additional_queries, self.budgets.max_queries)
                if not query_queue:
                    break

        should_search_online = mode == "online" or (
            mode == "hybrid" and (not coverage or any(value.status != "covered" for value in coverage))
        )
        online_queries: list[str] = []
        if should_search_online:
            await self._enter("online_search", "正在补充在线学术来源")
            online_queries = _online_queries(plan, coverage, question, self.budgets.max_queries)
            statuses = await self._search_online(online_queries, ledger, evidence_limit)
            await self._complete(
                "online_search",
                {"queries": online_queries, "evidence": len(ledger.entries()), "providers": len(statuses)},
            )
            await self._enter("gap_assessment", "正在复核全部证据")
            assessment = await self._assess(plan, ledger, mode, allow_local=False)
            coverage = assessment.coverage
            await self._emit_coverage(coverage)
            await self._complete(
                "gap_assessment",
                {"next_action": "synthesize", "rationale": assessment.rationale},
            )

        evidence = ledger.evidence(max(evidence_limit, 20))
        if not evidence:
            raise GenerationError("no_evidence", "No local or online evidence matched the question")

        approvals = _acquisition_candidates(evidence)
        if approvals:
            await self._emit(
                "approval_available",
                {
                    "action": "import_dois",
                    "dois": approvals,
                    "reason": "可将在线文献加入当前文库并继续获取开放全文",
                },
            )
            evidence = await self._wait_for_acquisition(
                library_id,
                online_queries or plan.queries,
                ledger,
                evidence_limit,
            )

        await self._enter("synthesis", "正在综合研究结论")
        prompt = _build_prompt(question, evidence, mode, online_queries, history)
        prompt += "\n\nSubquestion coverage:\n" + json.dumps(
            [value.model_dump() for value in coverage], ensure_ascii=False
        )
        if self.steering:
            prompt += "\n\nUser steering instructions:\n" + json.dumps(self.steering, ensure_ascii=False)
        draft = await self._generate("synthesizer", SYSTEM_PROMPT, prompt, DraftAnswer)
        draft = _validated_draft(draft, evidence)
        await self._complete("synthesis", {"citations": draft.citation_ids})

        await self._enter("verification", "正在核验回答和引用")
        review = await self._review(question, plan, coverage, evidence, draft)
        await self._emit(
            "review_ready",
            {"blocking": len(review.blocking), "warnings": len(review.warnings)},
        )
        await self._complete(
            "verification",
            {"blocking": len(review.blocking), "warnings": len(review.warnings)},
        )

        if review.blocking and self.budgets.max_revision_rounds > 0:
            await self._enter("revision", "正在根据审查结果修订")
            draft = await self._revise(question, evidence, draft, review)
            draft = _validated_draft(draft, evidence)
            await self._complete("revision", {"citations": draft.citation_ids})
            await self._enter("verification", "正在执行最终引用检查")
            deterministic = _deterministic_review(draft, evidence, coverage)
            if deterministic.blocking:
                draft.limitations.extend(issue.reason for issue in deterministic.blocking)
            await self._complete(
                "verification",
                {"blocking": len(deterministic.blocking), "deterministic": True},
            )

        failed_sources = sorted({value.source for value in statuses if value.status == "failed"})
        if failed_sources:
            self.limitations.append(f"本轮不可用的在线来源：{', '.join(failed_sources)}")
        incomplete = [value.question for value in coverage if value.status != "covered"]
        if incomplete:
            self.limitations.append(f"证据仍不充分的子问题：{'；'.join(incomplete)}")
        limitations = list(dict.fromkeys([*draft.limitations, *self.limitations]))
        cited = [value for value in evidence if value.id in draft.citation_ids]
        metrics = {
            "model_steps": self.model_steps,
            "tool_calls": self.tool_calls,
            "local_rounds": local_rounds,
            "evidence_total": len(evidence),
            "evidence_cited": len(cited),
            "elapsed_ms": int((time.monotonic() - self.started_at) * 1000),
        }
        await self._stream_answer(draft.answer)
        self.state.transition("completed")
        await self._emit("result_ready", {"metrics": metrics})
        return AgentAnswer(
            draft.answer,
            cited,
            draft.citation_ids,
            limitations,
            self.gateway.model,
            online_queries,
            statuses,
            plan.model_dump(),
            [value.model_dump() for value in coverage],
            metrics,
            evidence,
        )

    async def _plan(
        self,
        question: str,
        history: list[ConversationTurn],
        session_memory: dict[str, Any],
    ) -> ResearchPlan:
        context = {
            "question": question,
            "recent_user_context": [value.content for value in history if value.role == "user"][-3:],
            "session_memory": session_memory,
            "limits": {
                "max_subquestions": self.budgets.max_subquestions,
                "max_queries": self.budgets.max_queries,
            },
        }
        try:
            plan = await self._generate(
                "planner", PLANNER_PROMPT, json.dumps(context, ensure_ascii=False), ResearchPlan
            )
        except GenerationError as exc:
            self.limitations.append(f"研究计划模型降级：{exc}")
            plan = ResearchPlan(
                intent=question,
                subquestions=[ResearchSubquestion(id="Q1", question=question)],
                queries=[question],
                completion_criteria=["使用可核验证据回答问题"],
            )
        return plan.model_copy(
            update={
                "subquestions": plan.subquestions[: self.budgets.max_subquestions],
                "queries": _unique_queries(plan.queries, self.budgets.max_queries) or [question],
            }
        )

    async def _search_local(
        self,
        library_id: str,
        queries: list[str],
        ledger: EvidenceLedger,
    ) -> None:
        async def search(query: str):
            self.tool_calls += 1
            return query, await self.retrieval.search(query, library_id, self.budgets.per_query_limit)

        await self._emit("tool_started", {"tool": "search_library", "queries": queries})
        if self.budgets.parallel_scouts:
            results = await asyncio.gather(*(search(query) for query in queries), return_exceptions=True)
        else:
            results = []
            for query in queries:
                try:
                    results.append(await search(query))
                except Exception as exc:  # noqa: BLE001 - provider errors are surfaced as run limitations
                    results.append(exc)
        for result in results:
            self.signal.raise_if_cancelled()
            if isinstance(result, BaseException):
                self.limitations.append(f"一个本地检索式执行失败：{result}")
                continue
            query, hits = result
            ledger.add_local(query, hits)
        await self._emit("tool_completed", {"tool": "search_library", "evidence": len(ledger.entries())})

    async def _search_online(
        self,
        queries: list[str],
        ledger: EvidenceLedger,
        evidence_limit: int,
    ) -> list[ProviderStatus]:
        if not self.discovery:
            self.limitations.append("在线搜索未配置")
            return []
        statuses: list[ProviderStatus] = []
        await self._emit("tool_started", {"tool": "search_online", "queries": queries})
        for query in queries:
            self.signal.raise_if_cancelled()
            if self._timed_out():
                self.limitations.append("达到研究时间预算，停止继续联网检索")
                break
            self.tool_calls += 1
            result = await self.discovery.search_with_status(query, max(3, min(evidence_limit, 10)))
            ledger.add_online(query, result.records)
            statuses.extend(result.providers)
        await self._emit("tool_completed", {"tool": "search_online", "evidence": len(ledger.entries())})
        return statuses

    async def _assess(
        self,
        plan: ResearchPlan,
        ledger: EvidenceLedger,
        mode: ResearchMode,
        *,
        allow_local: bool,
    ) -> GapAssessment:
        evidence = ledger.summary()
        payload = {
            "mode": mode,
            "allow_local_search": allow_local,
            "subquestions": [value.model_dump() for value in plan.subquestions],
            "evidence": evidence,
            "user_steering": self.steering,
            "scout_findings": [value.model_dump() for value in self.scout_findings],
        }
        try:
            result = await self._generate(
                "assessor", ASSESSOR_PROMPT, json.dumps(payload, ensure_ascii=False), GapAssessment
            )
        except GenerationError as exc:
            self.limitations.append(f"证据覆盖评估模型降级：{exc}")
            status = "partial" if evidence else "insufficient_evidence"
            return GapAssessment(
                coverage=[
                    CoverageItem(
                        subquestion_id=value.id,
                        question=value.question,
                        status=status,
                        required_level=value.required_level,
                        evidence_ids=[entry["id"] for entry in evidence[:3]],
                    )
                    for value in plan.subquestions
                ],
                next_action="online_search" if mode != "local" else "synthesize",
                rationale="Coverage assessor unavailable; used deterministic fallback.",
            )
        allowed = {entry.evidence.id for entry in ledger.entries()}
        normalized: list[CoverageItem] = []
        by_id = {value.id: value for value in plan.subquestions}
        for item in result.coverage:
            source = by_id.get(item.subquestion_id)
            if not source:
                continue
            ids = [value for value in item.evidence_ids if value in allowed]
            status = item.status
            if status == "covered" and not ids:
                status = "insufficient_evidence"
            normalized.append(
                item.model_copy(
                    update={
                        "question": source.question,
                        "required_level": source.required_level,
                        "evidence_ids": ids,
                        "status": status,
                    }
                )
            )
        seen = {value.subquestion_id for value in normalized}
        for source in plan.subquestions:
            if source.id not in seen:
                normalized.append(
                    CoverageItem(
                        subquestion_id=source.id,
                        question=source.question,
                        status="insufficient_evidence",
                        required_level=source.required_level,
                    )
                )
        action = result.next_action
        if action == "local_search" and not allow_local:
            action = "online_search" if mode != "local" else "synthesize"
        return result.model_copy(update={"coverage": normalized, "next_action": action})

    async def _review(
        self,
        question: str,
        plan: ResearchPlan,
        coverage: list[CoverageItem],
        evidence: list[Evidence],
        draft: DraftAnswer,
    ) -> ReviewResult:
        deterministic = _deterministic_review(draft, evidence, coverage)
        if deterministic.blocking:
            return deterministic
        payload = {
            "question": question,
            "subquestions": [value.model_dump() for value in plan.subquestions],
            "coverage": [value.model_dump() for value in coverage],
            "draft": draft.model_dump(),
            "evidence": [_review_evidence(value) for value in evidence],
        }
        try:
            review = await self._generate(
                "reviewer", REVIEWER_PROMPT, json.dumps(payload, ensure_ascii=False), ReviewResult
            )
        except GenerationError as exc:
            self.limitations.append(f"语义引用审查不可用：{exc}")
            return deterministic
        allowed = {value.id for value in evidence}
        valid = [value for value in review.valid_citation_ids if value in allowed]
        return review.model_copy(update={"valid_citation_ids": valid})

    async def _revise(
        self,
        question: str,
        evidence: list[Evidence],
        draft: DraftAnswer,
        review: ReviewResult,
    ) -> DraftAnswer:
        payload = {
            "question": question,
            "draft": draft.model_dump(),
            "review": review.model_dump(),
            "evidence": [_review_evidence(value, include_text=True) for value in evidence],
        }
        return await self._generate(
            "reviser", REVISION_PROMPT, json.dumps(payload, ensure_ascii=False), DraftAnswer
        )

    async def _run_scouts(self, plan: ResearchPlan, ledger: EvidenceLedger) -> None:
        if not self.budgets.parallel_scouts or not ledger.entries() or self.scout_rounds >= 2:
            return
        self.scout_rounds += 1
        evidence = ledger.summary(limit=18)
        available_steps = max(0, self.budgets.max_model_steps - self.model_steps - 3)
        subquestions = plan.subquestions[: min(3, available_steps)]
        if not subquestions:
            return
        await self._emit(
            "scouts_started",
            {"count": len(subquestions), "round": self.scout_rounds},
        )

        async def scout(subquestion: ResearchSubquestion):
            payload = {
                "subquestion": subquestion.model_dump(),
                "evidence": evidence,
                "user_steering": self.steering,
            }
            try:
                return await self._generate(
                    "scout",
                    SCOUT_PROMPT,
                    json.dumps(payload, ensure_ascii=False),
                    ScoutFinding,
                )
            except GenerationError as exc:
                self.limitations.append(f"Scout {subquestion.id} 未完成：{exc}")
                return None

        findings = await asyncio.gather(*(scout(value) for value in subquestions))
        allowed = {entry.evidence.id for entry in ledger.entries()}
        for finding in findings:
            if not finding:
                continue
            finding = finding.model_copy(
                update={"evidence_ids": [value for value in finding.evidence_ids if value in allowed]}
            )
            self.scout_findings.append(finding)
        await self._emit(
            "scouts_completed",
            {"count": len([value for value in findings if value]), "round": self.scout_rounds},
        )

    async def _wait_for_acquisition(
        self,
        library_id: str,
        queries: list[str],
        ledger: EvidenceLedger,
        evidence_limit: int,
    ) -> list[Evidence]:
        if not self.acquisition_source or self.budgets.acquisition_wait_seconds <= 0:
            return ledger.evidence(max(evidence_limit, 20))
        await self._enter("acquisition_wait", "等待确认导入与开放全文处理")
        deadline = time.monotonic() + min(
            self.budgets.acquisition_wait_seconds,
            max(0, self.budgets.soft_timeout_seconds - (time.monotonic() - self.started_at)),
        )
        outcome = "timeout"
        status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            self.signal.raise_if_cancelled()
            status = await self.acquisition_source()
            if status and status.get("decision") == "rejected":
                outcome = "skipped"
                break
            if status and status.get("decision") == "approved":
                await self._emit("acquisition_updated", status)
                if status.get("ready"):
                    await self._search_local(
                        library_id,
                        _unique_queries(queries, self.budgets.max_queries),
                        ledger,
                    )
                    await self._emit_evidence_summary(ledger)
                    outcome = "completed"
                    break
            await asyncio.sleep(0.5)
        if outcome == "timeout":
            self.limitations.append("开放全文导入未在本轮等待时间内完成；任务会在后台继续，可稍后重新调研。")
        await self._complete(
            "acquisition_wait",
            {"outcome": outcome, "status": status or {}},
        )
        return ledger.evidence(max(evidence_limit, 20))

    async def _generate(self, role: str, system: str, user: str, schema):
        self.signal.raise_if_cancelled()
        if self.model_steps >= self.budgets.max_model_steps:
            raise GenerationError("model_budget_exhausted", "Research model-step budget was exhausted")
        self.model_steps += 1
        step = self.model_steps
        await self._emit("model_started", {"role": role, "step": step})
        result = await self.gateway.generate_structured(role, system, user, schema, self.signal)
        await self._emit("model_completed", {"role": role, "step": step})
        return result

    async def _enter(self, phase: str, label: str) -> None:
        self.signal.raise_if_cancelled()
        await self._consume_steering()
        self.state.transition(phase)
        await self._emit("phase_started", {"phase": phase, "label": label})

    async def _complete(self, phase: str, output: dict[str, Any]) -> None:
        await self._emit("phase_completed", {"phase": phase, "output": output})

    async def _emit_evidence_summary(self, ledger: EvidenceLedger) -> None:
        entries = ledger.entries()
        levels: dict[str, int] = {}
        for entry in entries:
            levels[entry.level] = levels.get(entry.level, 0) + 1
        await self._emit("evidence_updated", {"count": len(entries), "levels": levels})

    async def _emit_coverage(self, coverage: list[CoverageItem]) -> None:
        counts = {"covered": 0, "partial": 0, "insufficient_evidence": 0}
        for value in coverage:
            counts[value.status] += 1
        await self._emit(
            "coverage_updated",
            {"counts": counts, "coverage": [value.model_dump() for value in coverage]},
        )

    async def _stream_answer(self, answer: str) -> None:
        for start in range(0, len(answer), 180):
            self.signal.raise_if_cancelled()
            await self._emit("answer_delta", {"delta": answer[start : start + 180]})

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink:
            await self.event_sink(event_type, payload)

    async def _consume_steering(self) -> None:
        if not self.steering_source:
            return
        messages = await self.steering_source()
        if not messages:
            return
        self.steering.extend(messages)
        await self._emit("steering_applied", {"messages": messages})

    def _timed_out(self) -> bool:
        return time.monotonic() - self.started_at >= self.budgets.soft_timeout_seconds


def _unique_queries(values: list[str], limit: int) -> list[str]:
    normalized = [" ".join(str(value).split()) for value in values if str(value).strip()]
    return list(dict.fromkeys(normalized))[:limit]


def _online_queries(
    plan: ResearchPlan,
    coverage: list[CoverageItem],
    question: str,
    limit: int,
) -> list[str]:
    missing = [query for value in coverage if value.status != "covered" for query in value.next_queries]
    return _unique_queries([*missing, *plan.queries, question], limit)


def _acquisition_candidates(evidence: list[Evidence]) -> list[str]:
    values: list[str] = []
    for item in evidence:
        if item.source_kind != "online" or not item.discovery_record:
            continue
        doi = str(item.discovery_record.get("doi") or "").strip().lower()
        if doi:
            values.append(doi)
    return list(dict.fromkeys(values))[:10]


def _validated_draft(draft: DraftAnswer, evidence: list[Evidence]) -> DraftAnswer:
    allowed = {value.id for value in evidence}
    invalid = [value for value in draft.citation_ids if value not in allowed]
    if invalid:
        raise GenerationError(
            "invalid_citations", f"Model cited evidence IDs that were not supplied: {', '.join(invalid)}"
        )
    in_text = list(dict.fromkeys(_CITATION_RE.findall(draft.answer)))
    invalid_text = [value for value in in_text if value not in allowed]
    if invalid_text:
        raise GenerationError(
            "invalid_citations",
            f"Answer contains evidence IDs that were not supplied: {', '.join(invalid_text)}",
        )
    used = [value for value in draft.citation_ids if value in in_text]
    if not used:
        raise GenerationError("missing_citations", "Model answer did not cite supplied evidence in its text")
    return draft.model_copy(update={"citation_ids": used})


def _deterministic_review(
    draft: DraftAnswer,
    evidence: list[Evidence],
    coverage: list[CoverageItem],
) -> ReviewResult:
    allowed = {value.id for value in evidence}
    in_text = set(_CITATION_RE.findall(draft.answer))
    issues = []
    for citation_id in set(draft.citation_ids) | in_text:
        if citation_id not in allowed:
            from researchbrain.orchestration.models import ReviewIssue

            issues.append(
                ReviewIssue(
                    type="invalid_citation",
                    claim=citation_id,
                    citation_ids=[citation_id],
                    reason=f"引用 {citation_id} 不在本次证据账本中",
                )
            )
    missing = [value.subquestion_id for value in coverage if value.status == "insufficient_evidence"]
    return ReviewResult(
        blocking=issues,
        missing_subquestions=missing,
        valid_citation_ids=[value for value in draft.citation_ids if value in allowed],
    )


def _review_evidence(evidence: Evidence, *, include_text: bool = False) -> dict[str, Any]:
    level = (
        "structured_abstract"
        if evidence.discovery_record and evidence.discovery_record.get("abstract")
        else "metadata"
    )
    if evidence.source_kind == "local" and not evidence.chunk_id.startswith("metadata:"):
        level = "fulltext_page" if evidence.page_start is not None else "fulltext_section"
    payload = {
        "id": evidence.id,
        "title": evidence.title,
        "level": level,
        "section": evidence.section,
        "page_start": evidence.page_start,
    }
    if include_text:
        payload["text"] = evidence.text[:1800]
    return payload
