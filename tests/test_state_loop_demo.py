from __future__ import annotations

from pathlib import Path

from experiments.state_loop_v0_1.run_minimal_loop import run


def test_minimal_state_loop_demo(tmp_path: Path) -> None:
    result = run(tmp_path / "state-loop-demo")

    assert result["passed"]
    assert result["state_after_proposal_only"]["version"] == result["current_state"]["version"]
    assert result["new_state"]["version"] == result["current_state"]["version"] + 1
    assert result["new_state"]["history"][-1]["proposal_id"] == result["state_patch"]["id"]
