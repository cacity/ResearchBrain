from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date
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
from researchbrain.orchestration.context import transform_context
from researchbrain.orchestration.evidence import EvidenceLedger
from researchbrain.orchestration.models import (
    CoverageItem,
    DraftAnswer,
    EvidenceRelevanceJudgment,
    EvidenceScreeningResult,
    GapAssessment,
    ResearchBudgets,
    ResearchPlan,
    ResearchSubquestion,
    ReviewResult,
    ScoutFinding,
)
from researchbrain.orchestration.state_machine import ResearchStateMachine
from researchbrain.orchestration.tools import (
    LocalSearchArguments,
    OnlineSearchArguments,
    RegisteredTool,
    ResearchToolRegistry,
)
from researchbrain.retrieval.service import EmbeddingPipeline

EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
SteeringSource = Callable[[], Awaitable[list[dict[str, str]]]]
AcquisitionSource = Callable[[], Awaitable[dict[str, Any] | None]]

PLANNER_PROMPT = """You are the planning stage of an evidence-grounded literature research system.
Decompose the task into answerable subquestions and produce concise multilingual academic queries.
Provide topic_terms containing discriminative Chinese and English terms that must occur in same-topic
evidence. Provide excluded_terms for likely homonyms or cross-domain meanings that must not be admitted
unless a topic term also occurs. Do not use generic terms such as data, analysis, method, processing,
or model.
Prior answer hypotheses and source identifiers are navigation hints only; they are not factual evidence.
Require full-text evidence for detailed methods, numerical results, figures, tables, equations, and pages.
Use structured_abstract for broad landscape questions and metadata only for bibliographic existence.
Interpret relative dates such as recent years from the supplied current_date.
Do not answer the research question. Return JSON matching the requested schema."""

ASSESSOR_PROMPT = """You are the evidence coverage assessor in a literature research system.
For each supplied subquestion, decide whether the evidence is covered, partial, or insufficient_evidence.
Only use listed evidence IDs. Respect evidence levels. Suggest focused missing queries when useful.
Choose local_search only when a new local query can plausibly close a gap; choose online_search when
external coverage is required; otherwise choose synthesize. Do not write the final answer.
Return JSON only."""

RELEVANCE_PROMPT = """You are the evidence admission gate for a rigorous literature research system.
Judge every supplied evidence item against the research intent and its subquestions.
- relevant: directly supports at least one subquestion in the same scientific topic or method context.
- adjacent: potentially useful background or analogy, but it cannot support an answer to the stated topic.
- irrelevant: only shares generic words, comes from a different discipline, or does not answer any
  subquestion.
Generic overlap such as data, analysis, processing, method, model, or system is never enough. For example,
multibeam sonar preprocessing is not evidence for spherical harmonic analysis unless the question explicitly
asks about multibeam sonar. Assign valid subquestion IDs only to relevant evidence. Judge all evidence IDs
exactly once.
Do not answer the research question. Return structured JSON only."""

REVIEWER_PROMPT = """You are an independent reviewer of an evidence-grounded literature answer.
Find unsupported factual claims, invalid citations, evidence-level violations, contradictions, and unanswered
subquestions. Metadata proves only bibliographic facts. Abstracts do not prove figure, page, equation, exact
parameter, or detailed workflow claims. Treat a citation from a different scientific topic as blocking even
when the citation ID exists. Check whether each cited excerpt actually entails the nearby claim.
Return structured JSON only. Do not rewrite the answer."""

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
        self.tools = ResearchToolRegistry(
            signal=self.signal,
            event_sink=self._emit,
            max_calls=self.budgets.max_tool_calls,
        )
        self.tools.register(
            RegisteredTool(
                name="search_library",
                arguments=LocalSearchArguments,
                handler=self._tool_search_library,
            )
        )
        if self.discovery:
            self.tools.register(
                RegisteredTool(
                    name="search_online",
                    arguments=OnlineSearchArguments,
                    handler=self._tool_search_online,
                )
            )

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
        context = transform_context(conversation_history or [], session_memory or {})
        history = context.history
        memory = context.memory
        ledger = EvidenceLedger("E" if mode == "local" else "L")
        statuses: list[ProviderStatus] = []

        await self._enter("intake", "正在理解问题")
        self.signal.raise_if_cancelled()
        await self._emit(
            "context_transformed",
            {
                "history_messages": len(history),
                "memory_identifiers": len(memory["source_identifiers"]),
                "prior_answers_are_evidence": False,
            },
        )
        await self._complete("intake", {"mode": mode, "history_messages": len(history)})

        await self._enter("planning", "正在拆分研究问题")
        plan = await self._plan(question, history, memory)
        await self._emit(
            "plan_ready",
            {
                "subquestions": [value.model_dump() for value in plan.subquestions],
                "queries": plan.queries,
                "topic_terms": plan.topic_terms,
                "excluded_terms": plan.excluded_terms,
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
                await self._screen_evidence(question, plan, ledger)
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
            await self._screen_evidence(question, plan, ledger)
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
            if not ledger.evidence(max(evidence_limit, 20), include_excluded=True):
                raise GenerationError("no_evidence", "No local or online evidence matched the question")
            raise GenerationError(
                "no_relevant_evidence",
                "Retrieved candidates were rejected by the topic relevance gate",
            )

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
            await self._screen_evidence(question, plan, ledger)
            evidence = ledger.evidence(max(evidence_limit, 20))

        if not evidence:
            raise GenerationError(
                "no_relevant_evidence",
                "No relevant evidence remained after acquisition screening",
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
        draft, removed_terms = _enforce_draft_topic_contract(draft, question, plan, evidence)
        if removed_terms:
            self.limitations.append("主题门禁从初稿移除了跨领域内容：" + "、".join(removed_terms))
        await self._complete("synthesis", {"citations": draft.citation_ids})

        await self._enter("verification", "正在核验回答和引用")
        review = await self._review(question, plan, coverage, evidence, draft)
        await self._emit(
            "review_ready",
            {
                "blocking": len(review.blocking),
                "warnings": len(review.warnings),
                "issues": [
                    {
                        "type": issue.type,
                        "claim": issue.claim,
                        "citation_ids": issue.citation_ids,
                        "reason": issue.reason,
                    }
                    for issue in [*review.blocking, *review.warnings]
                ],
            },
        )
        await self._complete(
            "verification",
            {"blocking": len(review.blocking), "warnings": len(review.warnings)},
        )

        if review.blocking and self.budgets.max_revision_rounds > 0:
            await self._enter("revision", "正在根据审查结果修订")
            draft = await self._revise(question, evidence, draft, review)
            draft = _validated_draft(draft, evidence)
            draft, removed_terms = _enforce_draft_topic_contract(draft, question, plan, evidence)
            if removed_terms:
                self.limitations.append("主题门禁从修订稿移除了跨领域内容：" + "、".join(removed_terms))
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
            ledger.evidence(max(evidence_limit, 40), include_excluded=True),
        )

    async def _plan(
        self,
        question: str,
        history: list[ConversationTurn],
        session_memory: dict[str, Any],
    ) -> ResearchPlan:
        context = {
            "question": question,
            "current_date": date.today().isoformat(),
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
            plan = _fallback_plan(question, self.budgets.max_subquestions, self.budgets.max_queries)
        return plan.model_copy(
            update={
                "subquestions": plan.subquestions[: self.budgets.max_subquestions],
                "queries": _unique_queries(plan.queries, self.budgets.max_queries) or [question],
                "topic_terms": _topic_contract_terms(question, plan)[:20],
                "excluded_terms": _topic_contract_exclusions(question, plan)[:20],
            }
        )

    async def _search_local(
        self,
        library_id: str,
        queries: list[str],
        ledger: EvidenceLedger,
    ) -> None:
        await self._emit("tool_started", {"tool": "search_library", "queries": queries})
        results = await self.tools.execute_many(
            "search_library",
            [
                {
                    "library_id": library_id,
                    "query": query,
                    "limit": self.budgets.per_query_limit,
                }
                for query in queries
            ],
            parallel=self.budgets.parallel_scouts,
        )
        self.tool_calls = self.tools.call_count
        for result in results:
            self.signal.raise_if_cancelled()
            if not result.succeeded:
                self.limitations.append(f"一个本地检索式执行失败：{result.error}")
                continue
            query, hits = result.value
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
        runnable_queries: list[str] = []
        for query in queries:
            if self._timed_out():
                self.limitations.append("达到研究时间预算，停止继续联网检索")
                break
            runnable_queries.append(query)
        results = await self.tools.execute_many(
            "search_online",
            [{"query": query, "limit": max(3, min(evidence_limit, 10))} for query in runnable_queries],
            parallel=self.budgets.parallel_scouts,
        )
        self.tool_calls = self.tools.call_count
        for result in results:
            self.signal.raise_if_cancelled()
            if not result.succeeded:
                self.limitations.append(f"一个在线检索式执行失败：{result.error}")
                continue
            query, discovery_result = result.value
            ledger.add_online(query, discovery_result.records)
            statuses.extend(discovery_result.providers)
        await self._emit("tool_completed", {"tool": "search_online", "evidence": len(ledger.entries())})
        return statuses

    async def _tool_search_library(self, arguments: LocalSearchArguments):
        hits = await self.retrieval.search(
            arguments.query,
            arguments.library_id,
            arguments.limit,
        )
        return arguments.query, hits

    async def _tool_search_online(self, arguments: OnlineSearchArguments):
        if not self.discovery:
            raise GenerationError("online_search_unavailable", "Online search is not configured")
        result = await self.discovery.search_with_status(arguments.query, arguments.limit)
        return arguments.query, result

    async def _screen_evidence(
        self,
        question: str,
        plan: ResearchPlan,
        ledger: EvidenceLedger,
    ) -> None:
        candidates = ledger.summary(include_excluded=True)
        if not candidates:
            return
        await self._emit("evidence_screening_started", {"count": len(candidates)})
        payload = {
            "intent": plan.intent,
            "question": question,
            "subquestions": [value.model_dump() for value in plan.subquestions],
            "evidence": candidates,
        }
        try:
            result = await self._generate(
                "relevance",
                RELEVANCE_PROMPT,
                json.dumps(payload, ensure_ascii=False),
                EvidenceScreeningResult,
            )
            supplied = {entry["id"] for entry in candidates}
            by_id = {value.evidence_id: value for value in result.judgments if value.evidence_id in supplied}
        except GenerationError as exc:
            self.limitations.append(f"证据相关性筛选模型降级：{exc}")
            by_id = {}

        judgments: list[EvidenceRelevanceJudgment] = []
        subquestion_ids = {value.id for value in plan.subquestions}
        for entry in candidates:
            judgment = by_id.get(entry["id"])
            if judgment:
                valid_subquestions = [value for value in judgment.subquestion_ids if value in subquestion_ids]
                if judgment.relevance != "relevant":
                    valid_subquestions = []
                judgment = judgment.model_copy(update={"subquestion_ids": valid_subquestions})
            else:
                judgment = _deterministic_relevance(entry, question, plan)
            judgment = _enforce_evidence_topic_contract(entry, judgment, question, plan)
            judgments.append(judgment)
        ledger.apply_screening(judgments)
        await self._emit(
            "evidence_screened",
            {
                "counts": ledger.screening_counts(),
                "judgments": [
                    {
                        "evidence_id": value.evidence_id,
                        "relevance": value.relevance,
                        "reason": value.reason,
                    }
                    for value in judgments
                ],
            },
        )

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
            fallback_coverage: list[CoverageItem] = []
            for value in plan.subquestions:
                evidence_ids = ledger.evidence_ids_for_subquestion(value.id)
                status = "partial" if evidence_ids else "insufficient_evidence"
                fallback_coverage.append(
                    CoverageItem(
                        subquestion_id=value.id,
                        question=value.question,
                        status=status,
                        required_level=value.required_level,
                        evidence_ids=evidence_ids,
                        missing=[] if evidence_ids else ["缺少经过主题筛选的直接相关证据"],
                        next_queries=[] if evidence_ids else [value.question],
                    )
                )
            return GapAssessment(
                coverage=fallback_coverage,
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
            "evidence": [_review_evidence(value, include_text=True) for value in evidence],
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
        # Reserve model calls for gap assessment, possible online screening, synthesis, and review.
        available_steps = max(0, self.budgets.max_model_steps - self.model_steps - 6)
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


def _fallback_plan(question: str, max_subquestions: int, max_queries: int) -> ResearchPlan:
    normalized = " ".join(question.split()).strip(" ，。；;：:")
    concise = re.sub(r"^(请|麻烦)?(帮我|给我)?(调研|研究|分析|查找|查询)(一下)?[，,：:\s]*", "", normalized)
    clauses = [
        value.strip(" ，。；;：:")
        for value in re.split(r"[，,；;。]|(?:还有|以及|同时)", concise)
        if value.strip(" ，。；;：:")
    ]
    topic_source = clauses[0] if clauses else concise
    boundary = re.search(r"最近|近[一二三四五六七八九十\d]+年|有哪|有哪些|有那些|如何|怎么", topic_source)
    topic = (topic_source[: boundary.start()] if boundary else topic_source[:32]).strip()
    if len(topic) < 2:
        topic = topic_source[:32]

    useful_clauses = [
        value for value in clauses if not re.search(r"^(形成|生成|输出|写成).*(报告|综述)$", value)
    ] or [concise]
    subquestions: list[ResearchSubquestion] = []
    queries: list[str] = []
    for index, clause in enumerate(useful_clauses[:max_subquestions], 1):
        text = clause if topic in clause else f"{topic}：{clause}"
        required_level = (
            "fulltext_section"
            if re.search(r"数据|流程|方法|缺陷|问题|注意|结果|图|表|参数", text)
            else "structured_abstract"
        )
        subquestions.append(
            ResearchSubquestion(
                id=f"Q{index}",
                question=text[:500],
                required_level=required_level,
            )
        )
        queries.append(text)
    return ResearchPlan(
        intent=normalized[:1000],
        subquestions=subquestions,
        queries=_unique_queries(queries, max_queries) or [topic],
        topic_terms=_fallback_topic_terms(question),
        excluded_terms=_fallback_excluded_terms(question),
        completion_criteria=["每个结论由同一主题的可核验证据支持", "明确区分证据、综合判断和证据缺口"],
    )


_GENERIC_RETRIEVAL_TERMS = {
    "analysis",
    "data",
    "method",
    "methods",
    "model",
    "models",
    "processing",
    "research",
    "result",
    "results",
    "study",
    "system",
    "数据分析",
    "数据处理",
    "分析方法",
    "研究方法",
    "研究结果",
    "注意事项",
}

_TOPIC_FAMILIES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        (
            "球谐",
            "球面谐波",
            "spherical harmonic",
            "spherical harmonics",
            "gauss coefficient",
            "gauss coefficients",
        ),
        (
            "多波束",
            "多波束测深",
            "multibeam",
            "multibeam sonar",
            "bathymetric survey",
            "hydrographic survey",
            "xtf decoding",
            "hsx decoding",
        ),
    ),
)


def _normalized_term(value: str) -> str:
    return re.sub(r"[\s_\-/]+", " ", value.casefold()).strip()


def _contains_term(text: str, term: str) -> bool:
    normalized_text = _normalized_term(text)
    normalized = _normalized_term(term)
    if not normalized:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized):
        return normalized.replace(" ", "") in normalized_text.replace(" ", "")
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", normalized_text) is not None


def _fallback_topic_terms(question: str) -> list[str]:
    terms: list[str] = []
    for topic_terms, _ in _TOPIC_FAMILIES:
        if any(_contains_term(question, value) for value in topic_terms):
            terms.extend(topic_terms)
    terms.extend(sorted(_topic_anchors(question), key=lambda value: (-len(value), value)))
    return _distinct_terms(terms)


def _fallback_excluded_terms(question: str) -> list[str]:
    excluded: list[str] = []
    for topic_terms, conflicts in _TOPIC_FAMILIES:
        if any(_contains_term(question, value) for value in topic_terms) and not any(
            _contains_term(question, value) for value in conflicts
        ):
            excluded.extend(conflicts)
    return _distinct_terms(excluded)


def _distinct_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(str(value).split()).strip(" ,，。；;：:")
        normalized = _normalized_term(clean)
        if len(normalized) < 3 or normalized in seen or normalized in _GENERIC_RETRIEVAL_TERMS:
            continue
        seen.add(normalized)
        result.append(clean)
    return result


def _topic_contract_terms(question: str, plan: ResearchPlan) -> list[str]:
    return _distinct_terms([*plan.topic_terms, *_fallback_topic_terms(question)])


def _topic_contract_exclusions(question: str, plan: ResearchPlan) -> list[str]:
    values = _distinct_terms([*plan.excluded_terms, *_fallback_excluded_terms(question)])
    topic_terms = {_normalized_term(value) for value in _topic_contract_terms(question, plan)}
    result: list[str] = []
    for value in values:
        normalized = _normalized_term(value)
        if _contains_term(question, value) or normalized in topic_terms:
            continue
        if re.search(r"[\u4e00-\u9fff]", normalized) and len(normalized.replace(" ", "")) <= 6:
            if any(topic in normalized or normalized in topic for topic in topic_terms):
                continue
        result.append(value)
    return result


def _enforce_evidence_topic_contract(
    entry: dict[str, Any],
    judgment: EvidenceRelevanceJudgment,
    question: str,
    plan: ResearchPlan,
) -> EvidenceRelevanceJudgment:
    if judgment.relevance == "irrelevant":
        return judgment
    evidence_text = f"{entry.get('title', '')}\n{entry.get('excerpt', '')}"
    topic_terms = _topic_contract_terms(question, plan)
    exclusions = _topic_contract_exclusions(question, plan)
    matched_topics = [value for value in topic_terms if _contains_term(evidence_text, value)]
    matched_exclusions = [value for value in exclusions if _contains_term(evidence_text, value)]
    if matched_exclusions and not matched_topics:
        return EvidenceRelevanceJudgment(
            evidence_id=judgment.evidence_id,
            relevance="irrelevant",
            subquestion_ids=[],
            reason=(
                "确定性主题门禁排除跨领域候选：命中 "
                + "、".join(matched_exclusions[:3])
                + "，但未命中研究主题术语"
            ),
        )
    if judgment.relevance == "relevant" and topic_terms and not matched_topics:
        return EvidenceRelevanceJudgment(
            evidence_id=judgment.evidence_id,
            relevance="adjacent",
            subquestion_ids=[],
            reason="模型判为相关，但确定性主题门禁未发现任何研究主题术语，降级为相邻材料",
        )
    return judgment


def _topic_anchors(text: str) -> set[str]:
    lowered = " ".join(text.lower().split())
    anchors: set[str] = set()
    english = [
        value for value in re.findall(r"[a-z][a-z0-9-]{2,}", lowered) if value not in _GENERIC_RETRIEVAL_TERMS
    ]
    anchors.update(english)
    anchors.update(" ".join(english[index : index + 2]) for index in range(len(english) - 1))
    anchors.update(value.lower() for value in re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", text))

    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    suffixes = ("分析", "分离", "反演", "同化", "建模", "模型", "算法", "扰动", "磁场", "重力场")
    for suffix in suffixes:
        start = 0
        while True:
            index = chinese.find(suffix, start)
            if index < 0:
                break
            for prefix_length in range(2, 7):
                if index >= prefix_length:
                    candidate = chinese[index - prefix_length : index] + suffix
                    if candidate not in _GENERIC_RETRIEVAL_TERMS:
                        anchors.add(candidate)
            start = index + len(suffix)
    return {value for value in anchors if len(value) >= 3}


def _deterministic_relevance(
    entry: dict[str, Any],
    question: str,
    plan: ResearchPlan,
) -> EvidenceRelevanceJudgment:
    evidence_text = f"{entry.get('title', '')}\n{entry.get('excerpt', '')}".lower()
    matched_subquestions: list[str] = []
    for subquestion in plan.subquestions:
        anchors = _topic_anchors(f"{question}\n{subquestion.question}\n{' '.join(plan.queries)}")
        if any(anchor.lower() in evidence_text for anchor in anchors):
            matched_subquestions.append(subquestion.id)
    if matched_subquestions:
        return EvidenceRelevanceJudgment(
            evidence_id=str(entry["id"]),
            relevance="relevant",
            subquestion_ids=matched_subquestions,
            reason="保守降级规则检测到研究主题锚点的直接匹配",
        )
    return EvidenceRelevanceJudgment(
        evidence_id=str(entry["id"]),
        relevance="irrelevant",
        subquestion_ids=[],
        reason="相关性模型不可用，且未检测到研究主题锚点；为避免跨领域串题而排除",
    )


def _online_queries(
    plan: ResearchPlan,
    coverage: list[CoverageItem],
    question: str,
    limit: int,
) -> list[str]:
    missing = [query for value in coverage if value.status != "covered" for query in value.next_queries]
    planned = _unique_queries([*missing, *plan.queries], limit)
    return planned or [" ".join(question.split())]


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


def _enforce_draft_topic_contract(
    draft: DraftAnswer,
    question: str,
    plan: ResearchPlan,
    evidence: list[Evidence],
) -> tuple[DraftAnswer, list[str]]:
    """Remove cross-topic lines even if the generator reintroduces them from model priors."""
    # Final output uses only deterministic conflict families. Model-proposed exclusions
    # remain useful for evidence screening but are not trusted to delete answer text.
    exclusions = _fallback_excluded_terms(question)
    if not exclusions:
        return draft, []

    kept: list[str] = []
    removed: list[str] = []
    for line in draft.answer.splitlines():
        matches = [value for value in exclusions if _contains_term(line, value)]
        if matches:
            removed.extend(matches)
            continue
        kept.append(line)
    answer = "\n".join(kept).strip()
    if not answer:
        raise GenerationError(
            "no_relevant_evidence",
            "The generated answer only contained concepts excluded by the topic contract",
        )
    in_text = list(dict.fromkeys(_CITATION_RE.findall(answer)))
    allowed = {value.id for value in evidence}
    citation_ids = [value for value in draft.citation_ids if value in in_text and value in allowed]
    if not citation_ids:
        raise GenerationError(
            "missing_citations",
            "No valid citations remained after enforcing the topic contract",
        )
    return (
        draft.model_copy(update={"answer": answer, "citation_ids": citation_ids}),
        _distinct_terms(removed),
    )


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
