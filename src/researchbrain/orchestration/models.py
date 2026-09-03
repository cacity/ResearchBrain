from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EvidenceLevel = Literal[
    "fulltext_page",
    "fulltext_section",
    "structured_abstract",
    "metadata",
]
CoverageStatus = Literal["covered", "partial", "insufficient_evidence"]
EvidenceRelevance = Literal["relevant", "adjacent", "irrelevant"]
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


class ResearchBudgets(BaseModel):
    max_subquestions: int = Field(default=6, ge=1, le=10)
    max_local_rounds: int = Field(default=3, ge=1, le=5)
    max_queries: int = Field(default=6, ge=1, le=12)
    per_query_limit: int = Field(default=12, ge=5, le=30)
    evidence_limit: int = Field(default=20, ge=5, le=60)
    max_model_steps: int = Field(default=12, ge=3, le=12)
    max_tool_calls: int = Field(default=30, ge=5, le=60)
    max_revision_rounds: int = Field(default=1, ge=0, le=2)
    soft_timeout_seconds: int = Field(default=180, ge=30, le=600)
    acquisition_wait_seconds: int = Field(default=30, ge=0, le=120)
    parallel_scouts: bool = False


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
    citation_ids: list[str] = Field(default_factory=list)
    reason: str


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
