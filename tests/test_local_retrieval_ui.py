from pathlib import Path

import pytest

pytestmark = pytest.mark.research_local_retrieval_ui


def test_research_trace_explains_candidate_inclusion_and_exclusion_reasons():
    source = Path("desktop/src/App.tsx").read_text(encoding="utf-8")

    assert "setRunSelectedEvidenceReasons" in source
    assert 'value.relevance === "relevant"' in source
    assert "入选：${value.reason}" in source
    assert "排除" in source
    assert "相邻保留审计" in source
    assert 'aria-label="候选入选理由"' in source
    assert 'aria-label="候选排除理由"' in source


def test_evidence_detail_panel_shows_persisted_screening_reason():
    source = Path("desktop/src/App.tsx").read_text(encoding="utf-8")

    assert "selectedEvidence.relevance_reason" in source
    assert "入选理由" in source
    assert "排除理由" in source
