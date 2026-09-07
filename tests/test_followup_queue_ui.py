from pathlib import Path

import pytest

pytestmark = pytest.mark.research_followup_recovery


def test_follow_up_ui_distinguishes_current_steering_and_persistent_queue():
    app_source = Path("desktop/src/App.tsx").read_text(encoding="utf-8")
    api_source = Path("desktop/src/api.ts").read_text(encoding="utf-8")

    assert 'title="作为当前运行 Steering"' in app_source
    assert 'title="加入运行结束后的 Follow-up 队列"' in app_source
    assert 'aria-label="待执行 Follow-up 队列"' in app_source
    assert 'aria-label="上移 Follow-up"' in app_source
    assert 'aria-label="下移 Follow-up"' in app_source
    assert 'aria-label="删除 Follow-up"' in app_source
    assert "api.reorderFollowUps" in app_source
    assert "api.deleteFollowUp" in app_source
    assert "followUps:" in api_source
    assert "target_branch_id" in api_source
