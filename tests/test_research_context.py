import pytest

from researchbrain.agent.service import ConversationTurn
from researchbrain.orchestration.context import transform_context


@pytest.mark.research_context_compaction
def test_context_transform_prunes_and_marks_prior_answers_as_non_evidence():
    history = [ConversationTurn(role="user", content=f" question {index} ") for index in range(10)]
    context = transform_context(
        history,
        {
            "supported_findings": ["Previous model claim"],
            "source_identifiers": ["10.1000/example"],
        },
    )

    assert len(context.history) == 8
    assert context.history[0].content == "question 2"
    assert context.memory["prior_answer_hypotheses"] == ["Previous model claim"]
    assert context.memory["evidence_policy"] == "navigation_only_zero_evidentiary_weight"
    assert context.memory["summary_is_evidence"] is False
    assert context.memory["must_reread_original_evidence"] is True


@pytest.mark.research_context_compaction
def test_context_uses_token_budget_and_emits_structured_compaction_checkpoint():
    history = [
        ConversationTurn(role="user", content="请只研究中国近海，排除多波束声呐。 DOI:10.1234/keep"),
        ConversationTurn(role="assistant", content="旧回答 " + "很长的历史 " * 80),
        ConversationTurn(role="user", content="继续，但必须包含 arXiv:2401.01234 和 PMID:123456。"),
    ]

    context = transform_context(
        history,
        {
            "constraints": ["只研究中国近海", "排除多波束声呐"],
            "source_identifiers": ["10.5555/memory"],
            "summary_model": "unit-test-compactor",
            "summary_model_version": "2026-09-03",
        },
        message_limit=3,
        token_budget=90,
        checkpoint_threshold=0.5,
    )

    checkpoint = context.memory["compaction_checkpoint"]
    assert context.memory["tokenizer"] == "researchbrain-regex-token-estimator-v1"
    assert context.memory["estimated_context_tokens"] <= 90
    assert checkpoint["schema_version"] == 1
    assert checkpoint["generator_model"] == "unit-test-compactor"
    assert checkpoint["generator_version"] == "2026-09-03"
    assert checkpoint["summary_is_evidence"] is False
    assert checkpoint["must_reread_original_evidence"] is True
    assert checkpoint["coverage_boundary"]["kept_recent_messages"] == len(context.history)
    assert checkpoint["preserved_constraints"] == ["只研究中国近海", "排除多波束声呐"]
    assert context.memory["source_identifiers"] == [
        "10.5555/memory",
        "10.1234/keep",
        "arXiv:2401.01234",
        "PMID:123456",
    ]


@pytest.mark.research_context_compaction
def test_context_removes_hypotheses_invalidated_by_new_evidence():
    context = transform_context(
        [],
        {
            "supported_findings": [
                "10.1000/old claimed the workflow was complete",
                "10.1000/valid reported a limitation",
            ],
            "conflicting_evidence_identifiers": ["10.1000/old"],
            "source_identifiers": ["10.1000/old", "10.1000/valid"],
        },
    )

    assert context.memory["prior_answer_hypotheses"] == ["10.1000/valid reported a limitation"]
    assert context.memory["source_identifiers"] == ["10.1000/old", "10.1000/valid"]
    assert context.memory["must_reread_original_evidence"] is True
