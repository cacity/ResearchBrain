from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal

from researchbrain.agent.deepseek import DeepSeekClient, GenerationError
from researchbrain.discovery.service import DiscoveryRecord, LiteratureDiscovery, ProviderStatus
from researchbrain.retrieval.index import SearchHit
from researchbrain.retrieval.service import EmbeddingPipeline

ResearchMode = Literal["local", "hybrid", "online"]
HISTORY_RETRIEVAL_WEIGHT = 0.25
HISTORY_MESSAGE_LIMIT = 6

SYSTEM_PROMPT = """You are ResearchBrain, a rigorous evidence-grounded research analyst.

Evidence and epistemic rules:
- Use only the supplied evidence for factual literature claims. Never invent sources,
  identifiers, methods, results, figures, page numbers, data access, or software capabilities.
- Evidence IDs beginning with L are from the selected local library. IDs beginning with W are
  online metadata or abstracts retrieved during this request. E IDs are local-only evidence.
- Online title/abstract evidence is not proof that the full paper was read. Do not attribute
  figure, equation, detailed method, or page-level claims to it.
- "Local knowledge" means knowledge supported by the selected local literature library. It does
  not prove that the user owns datasets, instruments, code, or a working model environment;
  local literature claims do NOT prove that the user owns those resources. Local results
  do NOT prove that the user owns the cited data or software.
- Distinguish reported findings, your synthesis, and proposed hypotheses. A proposal is not an
  established result. State exact evidence limitations instead of filling gaps.
- Conversation history is continuity context only and has zero evidentiary weight. Never use a
  previous assistant answer as factual support; re-establish factual claims from supplied evidence.

Answering rules:
- Identify every sub-question and answer all of them. For broad literature reviews, synthesize
  across papers rather than summarizing papers one by one.
- Compare themes by data, method, principal result, agreement or difference, and remaining gap.
- For proposed work, give the evidence basis, hypothesis, minimum required data, analysis method,
  observable or metric, falsification criterion, and expected contribution.
- Do not claim novelty solely because supplied evidence does not mention prior work.
- Use clear Markdown headings and compact tables or lists where useful.
- Every factual literature claim must cite one or more supplied IDs immediately as [L1], [W1],
  or [E1].

Return one valid JSON object only, with keys: answer (Markdown string), citation_ids
(array of evidence IDs actually used), and limitations (array of concise strings)."""

SEARCH_PLANNER_PROMPT = """Create a compact academic-search plan. Return JSON only with a
`queries` array. Include the original intent and useful English terminology or synonyms. Produce
at most 3 precise queries, without site: filters, Boolean syntax that is specific to one database,
or explanatory prose."""


@dataclass(frozen=True)
class Evidence:
    id: str
    chunk_id: str
    item_id: str
    title: str
    text: str
    section: str
    page_start: int | None
    page_end: int | None
    score: float
    source_kind: str = "local"
    source_name: str = "local-library"
    source_url: str = ""
    discovery_record: dict | None = None


@dataclass(frozen=True)
class AgentAnswer:
    answer: str
    evidence: list[Evidence]
    citation_ids: list[str]
    limitations: list[str]
    model: str
    search_queries: list[str] | None = None
    provider_statuses: list[ProviderStatus] | None = None


@dataclass(frozen=True)
class ConversationTurn:
    role: Literal["user", "assistant"]
    content: str


class ResearchAgent:
    def __init__(
        self,
        retrieval: EmbeddingPipeline,
        generator: DeepSeekClient,
        discovery: LiteratureDiscovery | None = None,
    ):
        self.retrieval = retrieval
        self.generator = generator
        self.discovery = discovery

    async def answer(
        self,
        library_id: str,
        question: str,
        limit: int = 10,
        mode: ResearchMode = "local",
        conversation_history: list[ConversationTurn] | None = None,
    ) -> AgentAnswer:
        if mode not in {"local", "hybrid", "online"}:
            raise ValueError(f"unsupported research mode: {mode}")

        history = (conversation_history or [])[-HISTORY_MESSAGE_LIMIT:]
        local_hits = []
        if mode != "online":
            local_hits = await self._retrieve_local(question, library_id, limit, history)

        search_queries: list[str] = []
        statuses: list[ProviderStatus] = []
        online_records: list[DiscoveryRecord] = []
        if mode != "local":
            if not self.discovery:
                raise GenerationError("online_search_unavailable", "Online search is not configured")
            search_queries = await self._plan_queries(question, history)
            for query in search_queries:
                result = await self.discovery.search_with_status(query, max(3, min(limit, 8)))
                statuses.extend(result.providers)
                online_records.extend(result.records)
            online_records = _deduplicate_online(online_records)[: max(limit, 15)]

        evidence: list[Evidence] = []
        local_prefix = "E" if mode == "local" else "L"
        evidence.extend(
            _to_local_evidence(index, hit, local_prefix) for index, hit in enumerate(local_hits, 1)
        )
        evidence.extend(_to_online_evidence(index, record) for index, record in enumerate(online_records, 1))
        if not evidence:
            raise GenerationError("no_evidence", "No local or online evidence matched the question")

        prompt = _build_prompt(question, evidence, mode, search_queries, history)
        result = await self.generator.generate_json(SYSTEM_PROMPT, prompt)
        answer = str(result.get("answer") or "").strip()
        if not answer:
            raise GenerationError("invalid_response", "DeepSeek returned an empty answer")
        raw_ids = result.get("citation_ids") or []
        citation_ids = list(dict.fromkeys(str(value) for value in raw_ids))
        allowed_ids = {value.id for value in evidence}
        invalid_ids = [value for value in citation_ids if value not in allowed_ids]
        if invalid_ids:
            raise GenerationError(
                "invalid_citations",
                f"DeepSeek cited evidence IDs that were not supplied: {', '.join(invalid_ids)}",
            )
        if not citation_ids:
            raise GenerationError("missing_citations", "DeepSeek answer did not cite supplied evidence")
        cited_evidence = [value for value in evidence if value.id in citation_ids]
        limitations = [str(value) for value in result.get("limitations") or []]
        failed_sources = sorted({value.source for value in statuses if value.status == "failed"})
        if failed_sources:
            limitations.append(f"Online sources unavailable during this request: {', '.join(failed_sources)}")
        return AgentAnswer(
            answer,
            cited_evidence,
            citation_ids,
            list(dict.fromkeys(limitations)),
            self.generator.model,
            search_queries,
            statuses,
        )

    async def _retrieve_local(
        self,
        question: str,
        library_id: str,
        limit: int,
        history: list[ConversationTurn],
    ) -> list[SearchHit]:
        current_hits = await self.retrieval.search(question, library_id, limit)
        previous_questions = [turn.content for turn in history if turn.role == "user"][-2:]
        if not previous_questions:
            return current_hits
        context_query = "\n".join(
            [
                f"Current question: {question}",
                "Recent user context:",
                *previous_questions,
            ]
        )
        context_hits = await self.retrieval.search(context_query, library_id, limit)
        return _weighted_hits(current_hits, context_hits, limit)

    async def _plan_queries(
        self,
        question: str,
        history: list[ConversationTurn],
    ) -> list[str]:
        recent_users = [turn.content for turn in history if turn.role == "user"][-2:]
        planner_input = question
        if recent_users:
            planner_input = f"Current question: {question}\nRecent user context: {' | '.join(recent_users)}"
        try:
            result = await self.generator.generate_json(SEARCH_PLANNER_PROMPT, planner_input)
            raw = result.get("queries") or []
            queries = [" ".join(str(value).split()) for value in raw if str(value).strip()]
            return list(dict.fromkeys([question, *queries]))[:3]
        except GenerationError:
            return [question]


def _to_local_evidence(index: int, hit: SearchHit, prefix: str = "E") -> Evidence:
    return Evidence(
        id=f"{prefix}{index}",
        chunk_id=hit.chunk_id,
        item_id=hit.item_id,
        title=hit.title,
        text=hit.text,
        section=hit.section,
        page_start=hit.page_start,
        page_end=hit.page_end,
        score=hit.score,
    )


def _to_evidence(index: int, hit: SearchHit) -> Evidence:
    return _to_local_evidence(index, hit)


def _to_online_evidence(index: int, record: DiscoveryRecord) -> Evidence:
    identifiers = record.identifiers or {}
    identity = "; ".join(f"{key.upper()}: {value}" for key, value in identifiers.items())
    fields = [f"Title: {record.title}"]
    if record.authors:
        fields.append(f"Authors: {', '.join(record.authors)}")
    if record.venue:
        fields.append(f"Venue: {record.venue}")
    if record.year:
        fields.append(f"Year: {record.year}")
    if identity:
        fields.append(identity)
    if record.abstract:
        fields.append(f"Abstract: {record.abstract}")
    return Evidence(
        id=f"W{index}",
        chunk_id=f"web:{record.source}:{record.source_id}",
        item_id="",
        title=record.title,
        text="\n".join(fields),
        section="online title/abstract",
        page_start=None,
        page_end=None,
        score=1.0,
        source_kind="online",
        source_name=", ".join(record.sources or [record.source]),
        source_url=record.url,
        discovery_record=asdict(record),
    )


def _build_prompt(
    question: str,
    evidence: list[Evidence],
    mode: ResearchMode = "local",
    search_queries: list[str] | None = None,
    conversation_history: list[ConversationTurn] | None = None,
) -> str:
    abstract_count = sum(
        _evidence_level(value) in {"title/abstract", "online title/abstract"} for value in evidence
    )
    fulltext_count = len(evidence) - abstract_count
    scope = {
        "local": "the currently selected local literature library only",
        "hybrid": "the selected local library plus live online academic search",
        "online": "live online academic search; the local library was not queried",
    }[mode]
    sections = [
        "Task:\n"
        f"{question}\n\n"
        "Evidence-set scope:\n"
        f"- Search scope: {scope}.\n"
        f"- Supplied records: {len(evidence)} ({abstract_count} title/abstract; "
        f"{fulltext_count} full-text excerpt).\n"
        "- Do not reinterpret literature data or model usage as resources owned by the user.\n"
        + (f"- Online search queries: {search_queries}.\n" if search_queries else "")
    ]
    history = (conversation_history or [])[-HISTORY_MESSAGE_LIMIT:]
    if history:
        continuity = [
            "Conversation continuity (not evidence):",
            "- Use this only to resolve intent, references, and requested follow-ups.",
            "- Do not cite it or treat prior assistant conclusions as factual support.",
        ]
        for turn in history:
            content = " ".join(turn.content.split())[:1200]
            continuity.append(f"{turn.role.upper()}: {content}")
        sections.append("\n".join(continuity))
    sections.append("Evidence:")
    for value in evidence:
        location = _location(value)
        entry = (
            f"[{value.id}] {value.title}{location}\n"
            f"Evidence source: {value.source_kind} / {value.source_name}\n"
            f"Evidence level: {_evidence_level(value)}\n"
            f"Source URL: {value.source_url or 'local library'}\n"
            f"{_evidence_excerpt(value.text)}"
        )
        sections.append(entry)
    sections.append(
        "Answer every part of the task. For broad research planning, use sections such as "
        "结论概览、已有研究图谱、当前证据支持的认识、可开展的工作、可检验研究假设 and "
        "证据边界. Return valid JSON only."
    )
    return "\n\n".join(sections)


def _evidence_level(evidence: Evidence) -> str:
    if evidence.source_kind == "online":
        return "online title/abstract"
    if evidence.chunk_id.startswith("metadata:") or evidence.section == "题录与摘要":
        return "title/abstract"
    return "full-text excerpt"


def _evidence_excerpt(text: str, limit: int = 1800) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}\n[Evidence excerpt truncated]"


def _location(evidence: Evidence) -> str:
    components: list[str] = []
    if evidence.section:
        components.append(evidence.section)
    if evidence.page_start:
        page = str(evidence.page_start)
        if evidence.page_end and evidence.page_end != evidence.page_start:
            page = f"{page}-{evidence.page_end}"
        components.append(f"p. {page}")
    return f" ({'; '.join(components)})" if components else ""


def _deduplicate_online(records: list[DiscoveryRecord]) -> list[DiscoveryRecord]:
    unique: dict[str, DiscoveryRecord] = {}
    for record in records:
        identifiers = record.identifiers or {}
        key = (
            identifiers.get("doi")
            or identifiers.get("pmid")
            or identifiers.get("arxiv")
            or " ".join(record.title.lower().split())
        )
        existing = unique.get(key)
        if not existing or len(record.abstract) > len(existing.abstract):
            unique[key] = record
    return list(unique.values())


def _weighted_hits(
    current_hits: list[SearchHit],
    context_hits: list[SearchHit],
    limit: int,
) -> list[SearchHit]:
    by_id: dict[str, SearchHit] = {}
    scores: dict[str, float] = {}
    for weight, hits in ((1.0, current_hits), (HISTORY_RETRIEVAL_WEIGHT, context_hits)):
        for rank, hit in enumerate(hits, 1):
            by_id.setdefault(hit.chunk_id, hit)
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + weight / (60 + rank)
    ordered = sorted(by_id.values(), key=lambda hit: scores[hit.chunk_id], reverse=True)
    return [replace(hit, score=scores[hit.chunk_id]) for hit in ordered[:limit]]
