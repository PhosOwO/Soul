from __future__ import annotations

from pathlib import Path

from tests.cognitive_diff_adapter import run_cognitive_diff_case


def test_state_patch_signal_creates_patch_diff() -> None:
    case = {
        "scope": "Soul Architecture",
        "episode": [
            {
                "role": "user",
                "content": "我们决定 Soul 不再保留 Entity/Candidate 兼容路径，核心只走 State Patch。",
            }
        ],
        "expected": {},
    }

    actual = run_cognitive_diff_case(case)

    assert actual["diff_type"] == "STATE_PATCH_PROPOSAL"
    assert actual["should_create_patch"] is True
    assert actual["should_create_candidate"] is False
    assert actual["should_create_entity"] is False
    assert actual["requires_user_confirmation"] is True


def test_task_request_stays_no_change() -> None:
    case = {
        "scope": "Evaluation",
        "episode": [
            {
                "role": "user",
                "content": "I need to complete MHW category evaluation and draw the confusion matrix.",
            }
        ],
        "expected": {},
    }

    actual = run_cognitive_diff_case(case)

    assert actual["diff_type"] == "NO_CHANGE"
    assert actual["should_create_patch"] is False
