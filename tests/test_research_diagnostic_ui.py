from pathlib import Path

import pytest

pytestmark = pytest.mark.research_diagnostics_ui


def test_research_diagnostic_ui_surfaces_action_claim_pending_and_export_paths():
    app_source = Path("desktop/src/App.tsx").read_text(encoding="utf-8")
    api_source = Path("desktop/src/api.ts").read_text(encoding="utf-8")

    assert "Action / Tool / Stop 时间线" in app_source
    assert "tool_result" in app_source
    assert "stop_decision" in app_source
    assert "Claim → Evidence span" in app_source
    assert "Steering 生效影响" in app_source
    assert "待审批 待澄清 后台任务 Follow-up" in app_source
    assert "exportResearchDiagnostics" in app_source
    assert "No secrets, API keys, raw full text" in app_source
    assert "claims?: Array" in api_source
    assert "tool_calls?: Array" in api_source
    assert "next_requirement?: string" in api_source


def test_research_ui_accessibility_narrow_long_answer_and_independent_scroll_contracts():
    app_source = Path("desktop/src/App.tsx").read_text(encoding="utf-8")
    styles = Path("desktop/src/styles.css").read_text(encoding="utf-8")

    assert 'aria-label="主导航"' in app_source
    assert 'aria-label="会话分支路径"' in app_source
    assert "aria-label={`查看证据 ${citation.id}，${evidenceLocation(citation)}`}" in app_source
    assert 'aria-label="补充研究要求"' in app_source
    assert 'aria-label="研究范围待澄清"' in app_source
    assert 'aria-label="回答研究范围澄清问题"' in app_source
    assert 'api.steerResearchRun(activeRunId, content, "clarification")' in app_source
    assert 'aria-live="polite"' in app_source
    assert '<details className="research-trace"' in app_source
    assert "overflow-wrap: anywhere" in styles
    assert ".message-list {" in styles and "overflow-y: auto" in styles
    assert ".evidence-panel {" in styles and "overflow-y: auto" in styles
    assert ".research-clarification {" in styles
    assert "overscroll-behavior: contain" in styles
    assert "@media (max-width: 900px)" in styles
    assert ".chat-layout {\n    grid-template-columns: 1fr;" in styles
    assert ".evidence-panel {\n    display: none;" in styles


def test_research_trace_uses_readable_text_sizes():
    styles = Path("desktop/src/styles.css").read_text(encoding="utf-8")

    trace_summary = styles.split(".research-trace summary {", 1)[1].split("}", 1)[0]
    trace_content = styles.split(".research-trace-content {", 1)[1].split("}", 1)[0]
    trace_heading = styles.split(
        ".research-trace-content section > strong {", 1
    )[1].split("}", 1)[0]

    assert "font-size: 12px;" in trace_summary
    assert "font-size: 12px;" in trace_content
    assert "line-height: 1.65;" in trace_content
    assert "font-size: 13px;" in trace_heading
