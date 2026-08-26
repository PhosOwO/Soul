from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

module = pytest.importorskip("experiments.state_loop_v0_1.run_minimal_loop")
module_path = Path(module.__file__ or "").resolve()
ROOT = Path(__file__).resolve().parents[1]
tracked = subprocess.run(
    ["git", "ls-files", "--error-unmatch", str(module_path.relative_to(ROOT))],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=False,
)
if tracked.returncode != 0:
    pytest.skip("state loop demo experiment is local ignored code, not a tracked test asset", allow_module_level=True)
run = module.run


def test_minimal_state_loop_demo(tmp_path: Path) -> None:
    result = run(tmp_path / "state-loop-demo")

    assert result["passed"]
    assert result["state_after_proposal_only"]["version"] == result["current_state"]["version"]
    assert result["new_state"]["version"] == result["current_state"]["version"] + 1
    assert result["new_state"]["history"][-1]["proposal_id"] == result["state_patch"]["id"]
