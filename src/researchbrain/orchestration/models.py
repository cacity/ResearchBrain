from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EvidenceLevel = Literal[
    "fulltext_page",
    "fulltext_section",
    "structured_abstract",
    "metadata",
]
CoverageStatus = Literal["covered", "partial", "insufficient_evidence"]
EvidenceRelevance = Literal["relevant", "adjacent", "irrelevant"]
StopStatus = Literal[
    "running",
    "ready_to_finish",
    "waiting",
    "limited",
    "failed",
    "cancelled",
]
ResearchTaskType = Literal[
    "literature_review",
    "method_review",
    "data_review",
    "comparison",
    "reproducibility",
    "fact_lookup",
    "other",
]
SubquestionType = Literal[
    "landscape",
    "people_and_work",
    "data",
    "method",
    "workflow",
    "result",
    "limitation",
    "comparison",
    "research_gap",
    "other",
]
QueryLanguage = Literal["zh", "en", "mixed"]
QuerySource = Literal["local", "crossref", "openalex", "arxiv", "pubmed", "all_online"]
AgentActionType = Literal["call_tools", "ask_user", "delegate", "synthesize", "review", "finish"]


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)
    permission: Literal["read", "write"] = "read"


class AgentAction(BaseModel):
    """One bounded, auditable decision made by the research Agent.

    Tool outputs are intentionally not part of this schema. They may only enter
    the run context as persisted ``ToolResultMessage`` records emitted by the
    registry after an actual tool execution.
    """

    model_config = ConfigDict(extra="forbid")

    action: AgentActionType
    rationale: str = Field(default="", max_length=1000)
    tool_calls: list[AgentToolCall] = Field(default_factory=list, max_length=12)
    question: str = Field(default="", max_length=2000)
    delegate_subquestion_ids: list[str] = Field(default_factory=list, max_length=10)
    answer: str = ""

    @model_validator(mode="after")
    def validate_action_payload(self) -> AgentAction:
        if self.action == "call_tools" and not self.tool_calls:
            raise ValueError("call_tools requires at least one structured tool call")
        if self.action != "call_tools" and self.tool_calls:
            raise ValueError("tool_calls are only valid for call_tools")
        if self.action == "ask_user" and not self.question.strip():
            raise ValueError("ask_user requires a question")
        if self.action == "delegate" and not self.delegate_subquestion_ids:
            raise ValueError("delegate requires at least one subquestion ID")
        if self.action == "finish" and not self.answer.strip():
            raise ValueError("finish requires an answer")
        return self


class ToolResultMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    tool: str
    status: Literal["completed", "failed"]
    result: object | None = None
    error: dict[str, str] | None = None


class ResearchBudgets(BaseModel):
    max_subquestions: int = Field(default=6, ge=1, le=10)
    max_local_rounds: int = Field(default=3, ge=1, le=5)
    max_online_rounds: int = Field(default=2, ge=1, le=4)
    max_queries: int = Field(default=6, ge=1, le=12)
    per_query_limit: int = Field(default=12, ge=5, le=30)
    evidence_limit: int = Field(default=20, ge=5, le=60)
    max_model_steps: int = Field(default=20, ge=3, le=30)
    max_tool_calls: int = Field(default=30, ge=5, le=60)
    max_revision_rounds: int = Field(default=1, ge=0, le=2)
    soft_timeout_seconds: int = Field(default=180, ge=30, le=600)
    acquisition_wait_seconds: int = Field(default=30, ge=0, le=120)
    clarification_wait_seconds: int = Field(default=120, ge=0, le=300)
    parallel_scouts: bool = False
    local_vector_weight: float = Field(default=1.0, ge=0.0, le=5.0)
    local_keyword_weight: float = Field(default=1.0, ge=0.0, le=5.0)
    local_metadata_weight: float = Field(default=1.0, ge=0.0, le=5.0)
    local_fulltext_weight: float = Field(default=1.0, ge=0.0, le=5.0)


class ResearchTimeRange(BaseModel):
    start_year: int | None = Field(default=None, ge=1000, le=3000)
    end_year: int | None = Field(default=None, ge=1000, le=3000)
    description: str = Field(default="", max_length=200)


class ResearchIntent(BaseModel):
    task_type: ResearchTaskType = "literature_review"
    normalized_question: str = Field(min_length=1, max_length=2000)
    domains: list[str] = Field(default_factory=list, max_length=12)
    research_objects: list[str] = Field(default_factory=list, max_length=20)
    methods: list[str] = Field(default_factory=list, max_length=20)
    data_requirements: list[str] = Field(default_factory=list, max_length=20)
    time_range: ResearchTimeRange = Field(default_factory=ResearchTimeRange)
    geography: list[str] = Field(default_factory=list, max_length=20)
    languages: list[str] = Field(default_factory=list, max_length=8)
    must_answer: list[str] = Field(default_factory=list, max_length=20)
    must_include: list[str] = Field(default_factory=list, max_length=20)
    must_exclude: list[str] = Field(default_factory=list, max_length=20)
    deliverables: list[str] = Field(default_factory=list, max_length=12)
    ambiguities: list[str] = Field(default_factory=list, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    clarification_required: bool = False


class ResearchSubquestion(BaseModel):
    id: str = Field(pattern=r"^Q\d+$")
    question: str = Field(min_length=1, max_length=500)
    type: SubquestionType = "other"
    priority: int = Field(default=3, ge=1, le=5)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    completion_criteria: list[str] = Field(default_factory=list, max_length=8)
    required_level: EvidenceLevel = "structured_abstract"


class QuerySpec(BaseModel):
    id: str = Field(pattern=r"^S\d+$")
    subquestion_id: str = Field(pattern=r"^Q\d+$")
    language: QueryLanguage
    source: QuerySource
    query: str = Field(min_length=1, max_length=500)
    concepts: list[str] = Field(default_factory=list, max_length=20)
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    abbreviations: list[str] = Field(default_factory=list, max_length=12)
    excluded_terms: list[str] = Field(default_factory=list, max_length=20)
    start_year: int | None = Field(default=None, ge=1000, le=3000)
    end_year: int | None = Field(default=None, ge=1000, le=3000)
    rationale: str = Field(default="", max_length=500)


class ResearchPlan(BaseModel):
    intent: str = Field(min_length=1, max_length=1000)
    research_intent: ResearchIntent | None = None
    subquestions: list[ResearchSubquestion] = Field(min_length=1, max_length=10)
    queries: list[str] = Field(min_length=1, max_length=12)
    query_specs: list[QuerySpec] = Field(default_factory=list, max_length=48)
    topic_terms: list[str] = Field(default_factory=list, max_length=20)
    excluded_terms: list[str] = Field(default_factory=list, max_length=20)
    completion_criteria: list[str] = Field(default_factory=list, max_length=10)


class CoverageItem(BaseModel):
    subquestion_id: str
    question: str
    status: CoverageStatus
    required_level: EvidenceLevel = "structured_abstract"
    evidence_ids: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    next_queries: list[str] = Field(default_factory=list, max_length=6)


class GapAssessment(BaseModel):
    coverage: list[CoverageItem]
    next_action: Literal["local_search", "online_search", "synthesize"]
    additional_queries: list[str] = Field(default_factory=list, max_length=6)
    rationale: str = ""


class StopDecision(BaseModel):
    stop: bool
    reason: str
    status: StopStatus = "running"
    next_requirement: str = ""


class EvidenceRelevanceJudgment(BaseModel):
    evidence_id: str = Field(pattern=r"^[ELW]\d+$")
    relevance: EvidenceRelevance
    subquestion_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)


class EvidenceScreeningResult(BaseModel):
    judgments: list[EvidenceRelevanceJudgment]


class ScoutFinding(BaseModel):
    subquestion_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list, max_length=8)
    missing: list[str] = Field(default_factory=list, max_length=8)
    next_queries: list[str] = Field(default_factory=list, max_length=4)


class SubagentBudget(BaseModel):
    max_steps: int = Field(default=2, ge=1, le=5)
    max_tool_calls: int = Field(default=2, ge=0, le=8)
    context_evidence_limit: int = Field(default=8, ge=1, le=20)


class SubagentTask(BaseModel):
    id: str = Field(pattern=r"^SA\d+$")
    subquestion: ResearchSubquestion
    strategy: Literal["local", "online", "evidence_only"] = "evidence_only"
    allowed_tools: list[str] = Field(default_factory=list)
    budget: SubagentBudget = Field(default_factory=SubagentBudget)
    assigned_sources: list[str] = Field(default_factory=list)


class SubagentResult(BaseModel):
    task_id: str
    subquestion_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list, max_length=8)
    missing: list[str] = Field(default_factory=list, max_length=8)
    next_queries: list[str] = Field(default_factory=list, max_length=4)
    tool_calls: int = 0
    steps: int = 0
    cancelled: bool = False


class ComparisonMatrixRow(BaseModel):
    object: str = ""
    data: str = ""
    method: str = ""
    workflow: str = ""
    result: str = ""
    limitation: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    contribution: str = ""
    evidence_id: str
    evidence_level: EvidenceLevel
    statement_type: Literal["reported", "synthesis", "inference"] = "reported"
    contradiction_group: str = ""
    contradiction_note: str = ""


class ComparisonMatrix(BaseModel):
    rows: list[ComparisonMatrixRow] = Field(default_factory=list)
    common_workflow_steps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    author_claimed_limitations: list[str] = Field(default_factory=list)
    inferred_limitations: list[str] = Field(default_factory=list)


class ClaimEvidenceSpan(BaseModel):
    evidence_id: str
    span: str = Field(max_length=800)
    support_reason: str = Field(default="", max_length=300)


class AnswerClaim(BaseModel):
    claim_id: str = Field(pattern=r"^C[0-9a-f]{10}$")
    text: str = Field(min_length=1, max_length=1000)
    citation_ids: list[str] = Field(default_factory=list)
    evidence_spans: list[ClaimEvidenceSpan] = Field(default_factory=list)
    claim_type: Literal["factual", "evidence_gap", "inference", "recommendation"] = "factual"
    support_status: Literal["supported", "needs_citation", "limited", "uncited_nonfactual"] = "limited"


class DraftAnswer(BaseModel):
    answer: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReviewIssue(BaseModel):
    type: Literal[
        "unsupported_claim",
        "invalid_citation",
        "evidence_level_violation",
        "missing_subquestion",
        "contradiction",
        "other",
    ]
    claim: str = ""
    claim_id: str = ""
    severity: Literal["blocking", "warning"] = "blocking"
    citation_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    suggestion: str = ""


class ReviewResult(BaseModel):
    blocking: list[ReviewIssue] = Field(default_factory=list)
    warnings: list[ReviewIssue] = Field(default_factory=list)
    missing_subquestions: list[str] = Field(default_factory=list)
    valid_citation_ids: list[str] = Field(default_factory=list)


class SessionMemorySummary(BaseModel):
    goal: str = ""
    constraints: list[str] = Field(default_factory=list)
    terminology: list[str] = Field(default_factory=list)
    supported_findings: list[str] = Field(default_factory=list)
    source_identifiers: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
