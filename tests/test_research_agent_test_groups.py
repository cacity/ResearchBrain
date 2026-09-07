from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.research_baseline


EXPECTED_RESEARCH_AGENT_GROUPS = {
    "research_baseline",
    "research_clarification",
    "research_local_retrieval_ui",
    "research_doi_pdf_loop",
    "research_agent_action_loop",
    "research_tool_policy_hooks",
    "research_steering_abort",
    "research_context_compaction",
    "research_session_branching",
    "research_subagent_loop",
    "research_followup_recovery",
    "research_diagnostics_ui",
    "research_release_acceptance",
}


def _registered_pytest_markers() -> set[str]:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    marker_lines = data["tool"]["pytest"]["ini_options"]["markers"]
    return {line.split(":", 1)[0] for line in marker_lines}


def test_unfinished_research_agent_modules_have_independent_pytest_groups():
    assert EXPECTED_RESEARCH_AGENT_GROUPS <= _registered_pytest_markers()


def test_research_agent_test_group_commands_are_documented():
    guide = Path("docs/research-agent-test-groups.md").read_text(encoding="utf-8")
    for marker in EXPECTED_RESEARCH_AGENT_GROUPS:
        assert f"-m {marker}" in guide


def test_implementation_checklist_links_test_group_guide():
    checklist = Path("docs/research-agent-implementation-checklist.md").read_text(encoding="utf-8")
    assert "docs/research-agent-test-groups.md" in checklist
