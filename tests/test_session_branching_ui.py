from pathlib import Path

import pytest

pytestmark = pytest.mark.research_session_branching


def test_branching_ui_shows_path_switcher_and_create_entrypoint():
    source = Path("desktop/src/App.tsx").read_text(encoding="utf-8")
    api_source = Path("desktop/src/api.ts").read_text(encoding="utf-8")

    assert "branchPath" in source
    assert 'aria-label="会话分支路径"' in source
    assert "createBranchFromMessage" in source
    assert "从这条用户消息创建分支" in source
    assert "session.parent_session_id" in source
    assert "createBranch" in api_source
    assert "branchPath" in api_source
    assert "archiveSession" in api_source
