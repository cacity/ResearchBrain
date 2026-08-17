import os

from researchbrain.lifecycle import process_is_running


def test_process_liveness_probe_handles_current_and_invalid_pid():
    assert process_is_running(os.getpid()) is True
    assert process_is_running(-1) is False
