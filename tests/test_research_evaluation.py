import pytest

from researchbrain.orchestration.evaluation import (
    build_blind_review_packet,
    score_research_result,
    summarize_quality_results,
    summarize_subagent_ab_results,
)

pytestmark = pytest.mark.research_baseline


def test_research_quality_score_detects_invalid_citations_and_partial_coverage():
    result = score_research_result(
        "Supported [E1], but invented [E9].",
        [{"id": "E1"}, {"id": "E2"}],
        [
            {"status": "covered"},
            {"status": "partial"},
            {"status": "insufficient_evidence"},
        ],
    )

    assert result["citation_id_valid"] is False
    assert result["invalid_citation_ids"] == ["E9"]
    assert result["uncited_payload_ids"] == ["E2"]
    assert result["coverage"]["ratio"] == 0.5


def test_research_quality_score_accepts_visible_uncited_readiness_response():
    result = score_research_result("当前文库没有可用证据。", [], [])

    assert result["citation_id_valid"] is True
    assert result["has_visible_answer"] is True


def test_research_quality_score_detects_cross_topic_terms():
    result = score_research_result(
        "错误地混入了多波束数据处理。",
        [],
        [],
        ["多波束", "multibeam"],
    )

    assert result["topic_relevance"] is False
    assert result["topic_violations"] == ["多波束"]


def test_fixed_quality_set_summary_records_coverage_citation_timing_and_model_call_baselines():
    results = [
        {
            "case": {"id": "local-data"},
            "implementation": "v2",
            "status": "completed",
            "elapsed_ms": 1200,
            "run_metrics": {"model_steps": 3, "tool_calls": 5},
            "score": {
                "citation_id_valid": True,
                "citation_id_valid_ratio": 1.0,
                "has_visible_answer": True,
                "topic_relevance": True,
                "coverage": {"ratio": 0.75},
            },
        },
        {
            "case": {"id": "local-results"},
            "implementation": "v2",
            "status": "completed",
            "elapsed_ms": 800,
            "run_metrics": {"model_steps": 1, "tool_calls": 3},
            "score": {
                "citation_id_valid": False,
                "citation_id_valid_ratio": 0.5,
                "has_visible_answer": True,
                "topic_relevance": True,
                "coverage": {"ratio": 0.25},
            },
        },
    ]

    summary = summarize_quality_results(results)
    v2 = summary["implementations"]["v2"]

    assert summary["case_count"] == 2
    assert v2["coverage_ratio_mean"] == 0.5
    assert v2["citation_id_valid_rate"] == 0.5
    assert v2["citation_id_valid_ratio_mean"] == 0.75
    assert v2["elapsed_ms_mean"] == 1000
    assert v2["model_steps_total"] == 4
    assert v2["tool_calls_total"] == 8


@pytest.mark.research_subagent_loop
def test_subagent_ab_summary_reports_benefit_and_cost_on_fixed_quality_set():
    results = [
        {
            "case": {"id": "case-1"},
            "variant": "subagent_off",
            "run_metrics": {"model_steps": 4, "tool_calls": 5},
            "score": {"coverage": {"ratio": 0.5}, "citation_id_valid_ratio": 1.0},
        },
        {
            "case": {"id": "case-1"},
            "variant": "subagent_on",
            "run_metrics": {"model_steps": 5, "tool_calls": 7, "scout_findings": [{"subquestion_id": "Q1"}]},
            "score": {"coverage": {"ratio": 0.75}, "citation_id_valid_ratio": 1.0},
        },
    ]

    summary = summarize_subagent_ab_results(results)

    assert summary["pair_count"] == 1
    assert summary["coverage_delta_mean"] == 0.25
    assert summary["score_delta_mean"] == 0.25
    assert summary["tool_call_delta_mean"] == 2
    assert summary["model_step_delta_mean"] == 1
    assert summary["benefit_positive_cases"] == 1
    assert summary["cost_increased_cases"] == 1
    assert summary["pairs"][0]["subagent_findings"] == 1


def test_blind_review_packet_is_reproducible_and_hides_implementation_labels_from_pairs():
    results = [
        {
            "case": {"id": "local-data", "question": "Q?", "mode": "local", "checks": ["coverage"]},
            "implementation": "v1",
            "status": "completed",
            "answer": "legacy",
            "citations": [],
            "limitations": [],
            "score": {"citation_id_valid": True},
        },
        {
            "case": {"id": "local-data", "question": "Q?", "mode": "local", "checks": ["coverage"]},
            "implementation": "v2",
            "status": "completed",
            "answer": "agent loop",
            "citations": [],
            "limitations": [],
            "score": {"citation_id_valid": True},
        },
    ]

    first = build_blind_review_packet(results, seed="fixed")
    second = build_blind_review_packet(results, seed="fixed")

    assert first == second
    assert first["pair_count"] == 1
    assert set(first["pairs"][0]["answers"]) == {"A", "B"}
    assert "implementation" not in first["pairs"][0]["answers"]["A"]
    assert first["hidden_key"] == second["hidden_key"]
    assert {first["hidden_key"][0]["A"], first["hidden_key"][0]["B"]} == {"v1", "v2"}
