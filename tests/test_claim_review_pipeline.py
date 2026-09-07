from researchbrain.agent.service import Evidence
from researchbrain.orchestration.models import (
    ComparisonMatrix,
    CoverageItem,
    DraftAnswer,
    ResearchIntent,
    ResearchPlan,
    ResearchSubquestion,
)
from researchbrain.orchestration.orchestrator import (
    _bounded_evidence_report,
    _build_comparison_matrix,
    _deterministic_review,
    _extract_answer_claims,
    _remove_blocking_claims,
    _requires_bounded_report_fallback,
)


def _plan() -> ResearchPlan:
    return ResearchPlan(
        intent="spherical harmonic analysis",
        research_intent=ResearchIntent(
            normalized_question="spherical harmonic analysis",
            domains=["geodesy"],
            research_objects=["spherical harmonic"],
            must_exclude=["multibeam sonar"],
        ),
        subquestions=[ResearchSubquestion(id="Q1", question="methods", type="method")],
        queries=["spherical harmonic"],
        topic_terms=["spherical harmonic"],
        excluded_terms=["multibeam sonar"],
    )


def test_claim_review_pipeline_checks_citation_level_entailment_topic_and_coverage():
    evidence = [
        Evidence(
            id="W1",
            chunk_id="web:crossref:1",
            item_id="",
            title="Metadata only",
            text="Title: Spherical harmonic analysis\nAbstract: The abstract discusses coefficients.",
            section="online title/abstract",
            page_start=None,
            page_end=None,
            score=1.0,
            source_kind="online",
            discovery_record={"abstract": "The abstract discusses coefficients."},
        ),
        Evidence(
            id="L1",
            chunk_id="c1",
            item_id="i1",
            title="Sonar paper",
            text="Multibeam sonar preprocessing uses bathymetry swath filters.",
            section="Methods",
            page_start=1,
            page_end=1,
            score=1.0,
        ),
    ]
    draft = DraftAnswer(
        answer=(
            "Figure 2 proves the coefficient workflow [W1]. "
            "Multibeam sonar preprocessing is the main method [L1]. "
            "A factual claim has no citation."
        ),
        citation_ids=["W1", "L1"],
    )
    coverage = [CoverageItem(subquestion_id="Q2", question="results", status="insufficient_evidence")]
    claims = _extract_answer_claims(draft, evidence, coverage)

    review = _deterministic_review(draft, evidence, coverage, claims, plan=_plan())

    assert any(issue.type == "evidence_level_violation" for issue in review.warnings)
    assert any("附近没有引用" in issue.reason for issue in review.blocking)
    assert any("排除概念" in issue.reason for issue in review.blocking)
    assert any(issue.type == "missing_subquestion" for issue in review.warnings)
    assert all(issue.claim_id or issue.type in {"missing_subquestion"} for issue in review.blocking)


def test_contradiction_reviewer_and_blocking_claim_deletion():
    evidence = [
        Evidence(
            id="L1",
            chunk_id="c1",
            item_id="i1",
            title="A",
            text="Result: model improved accuracy for spherical harmonic coefficients.",
            section="Results",
            page_start=1,
            page_end=1,
            score=1,
        ),
        Evidence(
            id="L2",
            chunk_id="c2",
            item_id="i2",
            title="B",
            text="Result: model lower accuracy for spherical harmonic coefficients under sparse data.",
            section="Results",
            page_start=2,
            page_end=2,
            score=1,
        ),
    ]
    matrix: ComparisonMatrix = _build_comparison_matrix(_plan(), evidence)
    draft = DraftAnswer(
        answer="The model improved accuracy [L1]. Unsupported factual sentence.", citation_ids=["L1"]
    )
    claims = _extract_answer_claims(draft, evidence, [])

    review = _deterministic_review(draft, evidence, [], claims, plan=_plan(), comparison_matrix=matrix)
    sanitized = _remove_blocking_claims(draft, review)

    assert any(issue.type == "contradiction" for issue in review.warnings)
    assert "Unsupported factual sentence" not in sanitized.answer
    assert sanitized.citation_ids == ["L1"]


def test_markdown_claim_extraction_ignores_structure_and_respects_section_semantics():
    evidence = [
        Evidence(
            id="W1",
            chunk_id="web:crossref:1",
            item_id="",
            title="TEC study",
            text="Abstract: The study used TEC maps and reported an anomaly.",
            section="online title/abstract",
            page_start=None,
            page_end=None,
            score=1,
            source_kind="online",
            discovery_record={"abstract": "The study used TEC maps and reported an anomaly."},
        )
    ]
    draft = DraftAnswer(
        answer=(
            "## TEC 调研报告\n\n"
            "### 已有研究\n\n"
            "| 文献 | 数据 | 结果 |\n"
            "| --- | --- | --- |\n"
            "| [W1] | TEC maps | reported an anomaly |\n\n"
            "### 可开展的工作\n\n"
            "1. 建议增加无震对照期。\n"
            "2. 使用独立测试集评价模型。\n\n"
            "### 可检验假设\n\n"
            "- ConvLSTM 可能改善时空异常识别。\n\n"
            "### 证据边界\n\n"
            "- 尚未发现能够证明地震预测有效的全文证据。"
        ),
        citation_ids=["W1"],
    )

    claims = _extract_answer_claims(draft, evidence, [])

    assert len(claims) == 5
    assert claims[0].citation_ids == ["W1"]
    assert claims[0].support_status == "supported"
    assert [claim.claim_type for claim in claims[1:3]] == ["recommendation", "recommendation"]
    assert claims[3].claim_type == "inference"
    assert claims[4].claim_type == "evidence_gap"
    assert not any(claim.support_status == "needs_citation" for claim in claims)


def test_excessive_review_deletion_falls_back_to_a_structured_evidence_report():
    evidence = [
        Evidence(
            id="W1",
            chunk_id="web:crossref:1",
            item_id="",
            title="TEC prediction study",
            text="Title: TEC prediction study\nAbstract: The study used TEC maps and an LSTM model.",
            section="online title/abstract",
            page_start=None,
            page_end=None,
            score=1,
            source_kind="online",
            discovery_record={
                "abstract": "The study used TEC maps and an LSTM model.",
                "year": 2024,
            },
        )
    ]
    plan = _plan().model_copy(
        update={"research_intent": _plan().research_intent.model_copy(update={"deliverables": ["调研报告"]})}
    )
    before = DraftAnswer(answer="## 报告\n\n" + "有证据约束的内容 [W1]。" * 100, citation_ids=["W1"])
    after = DraftAnswer(answer="- 结果 [W1]。\n\n### 下一步\n\n1.", citation_ids=["W1"])
    coverage = [
        CoverageItem(
            subquestion_id="Q1",
            question="使用了哪些数据和方法？",
            status="partial",
            evidence_ids=["W1"],
            missing=["全文方法细节"],
            next_queries=["TEC LSTM full text"],
        )
    ]

    assert _requires_bounded_report_fallback(before, after, plan)
    report = _bounded_evidence_report(
        "TEC research",
        plan,
        coverage,
        evidence,
        _build_comparison_matrix(plan, evidence),
        "审查删减过多。",
    )

    assert len(report.answer) > 600
    assert "### 2. 文献与证据表" in report.answer
    assert "### 3. 证据覆盖与缺口" in report.answer
    assert "TEC LSTM full text" in report.answer
    assert report.citation_ids == ["W1"]
