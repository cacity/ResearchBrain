from researchbrain.orchestration.evaluation import score_research_result


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
