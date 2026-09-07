from researchbrain.agent.service import Evidence
from researchbrain.orchestration.models import ResearchIntent, ResearchPlan, ResearchSubquestion
from researchbrain.orchestration.orchestrator import _build_comparison_matrix


def _plan() -> ResearchPlan:
    return ResearchPlan(
        intent="compare spherical harmonic methods",
        research_intent=ResearchIntent(
            task_type="comparison",
            normalized_question="compare spherical harmonic workflows and limitations",
            research_objects=["spherical harmonic"],
            must_answer=["who did what", "data workflow", "limitations"],
        ),
        subquestions=[
            ResearchSubquestion(id="Q1", question="Who did what?", type="people_and_work"),
            ResearchSubquestion(id="Q2", question="What workflow?", type="workflow"),
        ],
        queries=["spherical harmonic workflow"],
    )


def test_comparison_matrix_maps_authors_year_contribution_workflow_and_limit_types():
    evidence = [
        Evidence(
            id="L1",
            chunk_id="c1",
            item_id="i1",
            title="Paper A",
            text=(
                "Authors: Ada Lovelace, Grace Hopper\nYear: 2024\n"
                "The authors proposed a spherical harmonic least-squares method. "
                "Workflow: calibrate sensors -> solve coefficients -> validate residuals. "
                "Result: improved accuracy. Limitation: authors note limited low-degree data."
            ),
            section="Methods",
            page_start=3,
            page_end=4,
            score=1.0,
        ),
        Evidence(
            id="L2",
            chunk_id="c2",
            item_id="i2",
            title="Paper B",
            text=(
                "Authors: Katherine Johnson\nYear: 2025\n"
                "Method: weighted spherical harmonic inversion. "
                "Workflow: calibrate sensors -> solve coefficients -> compare baselines. "
                "Result: lower accuracy under sparse sampling. "
                "The system inference is that reproducibility is weak without code."
            ),
            section="Results",
            page_start=None,
            page_end=None,
            score=0.8,
        ),
    ]

    matrix = _build_comparison_matrix(_plan(), evidence)

    assert len(matrix.rows) == 2
    assert matrix.rows[0].authors == ["Ada Lovelace", "Grace Hopper"]
    assert matrix.rows[0].year == 2024
    assert matrix.rows[0].contribution.startswith("The authors proposed")
    assert matrix.rows[0].evidence_level == "fulltext_page"
    assert any("calibrate sensors" in value for value in matrix.common_workflow_steps)
    assert matrix.author_claimed_limitations == ["L1: Limitation: authors note limited low-degree data."]


def test_comparison_matrix_preserves_conflicting_results_for_reviewer():
    evidence = [
        Evidence(
            id="L1",
            chunk_id="c1",
            item_id="i1",
            title="Paper A",
            text="Result: model improved accuracy for spherical harmonic coefficients.",
            section="Results",
            page_start=1,
            page_end=1,
            score=1.0,
        ),
        Evidence(
            id="L2",
            chunk_id="c2",
            item_id="i2",
            title="Paper B",
            text="Result: model lower accuracy for spherical harmonic coefficients under sparse data.",
            section="Results",
            page_start=2,
            page_end=2,
            score=1.0,
        ),
    ]

    matrix = _build_comparison_matrix(_plan(), evidence)

    assert matrix.contradictions
    assert all(row.contradiction_note for row in matrix.rows)
    assert {row.statement_type for row in matrix.rows} == {"reported"}
