from __future__ import annotations

from dataclasses import asdict
from typing import Any

from soul.services.reflection import extract_cognitive_diffs


def _message_text(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": message.get("role"),
        "text": message.get("text", message.get("content", "")),
        "timestamp": message.get("timestamp"),
    }


def run_cognitive_diff_case(case: dict[str, Any]) -> dict[str, Any]:
    scope = {"id": 1, "name": case["scope"], "data_json": "{}"}
    messages = [_message_text(message) for message in case.get("episode", [])]

    diffs = extract_cognitive_diffs(messages, scope=scope, max_patches=5)
    diff_dicts = [asdict(diff) for diff in diffs]
    primary = diff_dicts[0] if diff_dicts else {"diff_type": "NO_CHANGE"}

    return {
        "diff_type": primary["diff_type"],
        "should_create_patch": any(diff["diff_type"] == "STATE_PATCH_PROPOSAL" for diff in diff_dicts),
        "should_create_candidate": False,
        "should_create_entity": False,
        "should_create_event": False,
        "requires_user_confirmation": bool(primary.get("requires_user_confirmation")),
        "expected_scope": scope["name"],
        "operation": primary.get("operation"),
        "diffs": diff_dicts,
    }
