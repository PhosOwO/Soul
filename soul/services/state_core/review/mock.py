from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from soul.services.state_core.proposals import propose_patch
from soul.services.state_core.state_store import append_patch_proposal, load_state
from soul.services.state_core.working_state import upsert_working_state_from_evidence


def create_review_mock_project(target_dir: Path, *, reset: bool = False) -> dict[str, Any]:
    if reset and target_dir.resolve() == Path.cwd().resolve():
        raise ValueError("Refusing to reset the current project as a review mock sandbox.")
    if reset and target_dir.exists():
        remove_mock_state(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(target_dir, project_name="Soul Review Mock")
    refs = [
        {
            "type": "reme_file_chunk",
            "path": "daily/2026-08-22/mock-review.md",
            "start_line": 1,
            "end_line": 8,
            "score": 3.2,
        }
    ]
    proposal = propose_patch(
        state,
        {
            "source": "mock-ui",
            "summary": "User corrected package manager preference to pnpm.",
            "evidence_refs": refs,
            "state_item": {
                "id": "mock-prefer-pnpm",
                "kind": "active_constraint",
                "statement": "Use pnpm for dependency commands in this project.",
                "priority": "high",
                "confidence": 0.92,
            },
        },
    )
    append_patch_proposal(proposal, target_dir)

    due = upsert_working_state_from_evidence(
        target_dir,
        {
            "source": "mock-ui",
            "task": "Review Card UI",
            "summary": "后续默认使用 pnpm。",
            "evidence_refs": refs,
            "working_state": {
                "route": "working_state",
                "statement": "后续默认使用 pnpm。",
                "reason": "用户明确纠正包管理器。",
                "scope": "dependency setup",
                "review_after": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "review_card": True,
            },
        },
        state=state,
    )
    conflict = upsert_working_state_from_evidence(
        target_dir,
        {
            "source": "mock-ui",
            "task": "Review Card UI",
            "summary": "当前不再把 Review Card 设计成完整 project state dashboard。",
            "evidence_refs": refs,
            "working_state": {
                "route": "working_state",
                "statement": "当前不再把 Review Card 设计成完整 project state dashboard。",
                "reason": "用户指出便签目的只是 confirm + needs_review，减少决策成本。",
                "scope": "Soul Review Card UX",
                "review_after": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            },
        },
        state={
            **state,
            "current_state": {
                **state["current_state"],
                "state_items": [
                    *state["current_state"].get("state_items", []),
                    {
                        "id": "mock-old-review-model",
                        "kind": "accepted_belief",
                        "statement": "Review Card should show a full project state dashboard.",
                    },
                ],
            },
        },
    )
    return {
        "project_dir": str(target_dir),
        "patch_id": proposal["id"],
        "working_ids": [
            due.get("item", {}).get("id"),
            conflict.get("item", {}).get("id"),
        ],
    }


def remove_mock_state(target_dir: Path) -> None:
    state_dir = target_dir / ".soul"
    if not state_dir.exists():
        return
    for path in sorted(state_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    state_dir.rmdir()
