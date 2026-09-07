from researchbrain.agent.service import Evidence
from researchbrain.orchestration.models import CoverageItem, DraftAnswer
from researchbrain.orchestration.orchestrator import _deterministic_review, _extract_answer_claims


def _evidence() -> list[Evidence]:
    return [
        Evidence(
            id="L1",
            chunk_id="c1",
            item_id="i1",
            title="Paper A",
            text="The method improved accuracy for spherical harmonic coefficients. The dataset is sparse.",
            section="Results",
            page_start=2,
            page_end=2,
            score=1.0,
        )
    ]


def test_extract_answer_claims_has_stable_ids_citations_and_evidence_spans():
    draft = DraftAnswer(
        answer=(
            "The method improved accuracy for spherical harmonic coefficients [L1]. "
            "Evidence is insufficient for runtime."
        )
    )
    coverage = [
        CoverageItem(
            subquestion_id="Q1",
            question="runtime",
            status="insufficient_evidence",
            evidence_ids=[],
        )
    ]

    claims = _extract_answer_claims(draft, _evidence(), coverage)

    assert len(claims) == 2
    assert claims[0].claim_id.startswith("C")
    assert claims[0].citation_ids == ["L1"]
    assert claims[0].evidence_spans[0].evidence_id == "L1"
    assert "improved accuracy" in claims[0].evidence_spans[0].span
    assert claims[0].support_status == "supported"
    assert claims[1].claim_type == "evidence_gap"
    assert claims[1].support_status == "uncited_nonfactual"
    assert _extract_answer_claims(draft, _evidence(), coverage)[0].claim_id == claims[0].claim_id


def test_deterministic_review_flags_uncited_facts_and_overused_citations():
    draft = DraftAnswer(
        answer=(
            "Claim one is factual [L1]. Claim two is factual [L1]. Claim three is factual [L1]. "
            "Claim four is factual [L1]. This factual sentence lacks a citation. "
            "建议后续增加数据。"
        ),
        citation_ids=["L1"],
    )

    claims = _extract_answer_claims(draft, _evidence(), [])
    review = _deterministic_review(draft, _evidence(), [], claims)

    assert any("附近没有引用" in issue.reason for issue in review.blocking)
    assert any("超过上限 3" in issue.reason for issue in review.warnings)
    recommendation = next(claim for claim in claims if claim.claim_type == "recommendation")
    assert recommendation.support_status == "uncited_nonfactual"
