from researchbrain.agent.service import ConversationTurn
from researchbrain.orchestration.context import transform_context


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
