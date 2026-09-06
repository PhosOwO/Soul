from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from soul.services.state_core.proposals import propose_patch
from soul.services.state_core.state_store import append_patch_proposal, load_state, save_state
from soul.services.state_core.working_state import upsert_working_state_from_evidence
from soul.services.shared.state_types import StateDoc


def create_review_mock_project(target_dir: Path, *, reset: bool = False) -> dict[str, Any]:
    if reset and target_dir.resolve() == Path.cwd().resolve():
        raise ValueError("Refusing to reset the current project as a review mock sandbox.")
    if reset and target_dir.exists():
        remove_mock_state(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(target_dir, project_name="Soul Review Card Integration")
    state = cast(StateDoc, {
        **state,
        "current_state": {
            **state["current_state"],
            "state_items": [
                *state["current_state"].get("state_items", []),
                {
                    "id": "mock-old-review-model",
                    "kind": "accepted_belief",
                    "statement": "Review Card should show all due Working State items as a project status dashboard.",
                    "status": "accepted",
                    "priority": "medium",
                    "confidence": 0.68,
                },
            ],
        },
    })
    save_state(state, target_dir)
    refs = [
        {
            "type": "reme_file_chunk",
            "path": "daily/2026-08-22/review-card-integration.md",
            "start_line": 12,
            "end_line": 38,
            "score": 4.1,
        }
    ]
    patch_ids: list[str] = []
    for item in [
        {
            "id": "mock-review-card-purpose",
            "kind": "active_constraint",
            "statement": "Review Card is a low-interruption confirm and needs-review queue, not a project dashboard.",
            "priority": "high",
            "confidence": 0.95,
            "summary": "User corrected the Review Card product goal to reduce decision cost.",
        },
        {
            "id": "mock-review-card-candidates",
            "kind": "accepted_belief",
            "statement": "Review Card should only surface high-value confirm or needs-review decisions.",
            "priority": "high",
            "confidence": 0.91,
            "summary": "The UI should avoid becoming a generic state summary panel.",
        },
        {
            "id": "mock-review-card-evidence",
            "kind": "active_constraint",
            "statement": "Review Card may show one-line decisions by default, but evidence refs must stay available on expand.",
            "priority": "high",
            "confidence": 0.9,
            "summary": "User wanted evidence available without turning the card into a summary dashboard.",
        },
        {
            "id": "mock-dsh-before-turn-hook",
            "kind": "open_question",
            "statement": "Which DeepSeek Harness hook can safely inject Soul Current State before the model call?",
            "priority": "medium",
            "confidence": 0.72,
            "summary": "DSH currently captures after-turn evidence, but before-turn context injection remains unsettled.",
        },
    ]:
        evidence = {
            "source": "mock-ui",
            "summary": item.pop("summary"),
            "evidence_refs": refs,
            "state_item": item,
        }
        proposal = propose_patch(state, evidence)
        append_patch_proposal(proposal, target_dir)
        patch_ids.append(proposal["id"])

    due = upsert_working_state_from_evidence(
        target_dir,
        {
            "source": "mock-ui",
            "task": "Review Card Integration",
            "summary": "Review Card UI should show two decision buckets: Ready to Confirm and Needs Review.",
            "evidence_refs": refs,
            "working_state": {
                "route": "working_state",
                "kind": "project_fact",
                "statement": "Review Card UI should show two decision buckets: Ready to Confirm and Needs Review.",
                "reason": "This matches the low-decision-cost interaction model.",
                "scope": "Review Card UI",
                "confidence": 0.8,
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
            "task": "Review Card Integration",
            "summary": "Do not show every due Working State item as a Review Card decision.",
            "evidence_refs": refs,
            "working_state": {
                "route": "working_state",
                "kind": "constraint",
                "statement": "Do not show every due Working State item as a Review Card decision.",
                "reason": "Plain due Working State without conflict or explicit review_card flag is too noisy.",
                "scope": "Soul Review Card UX",
                "confidence": 0.8,
                "review_after": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            },
        },
        state=state,
    )
    return {
        "project_dir": str(target_dir),
        "patch_id": patch_ids[0],
        "patch_ids": patch_ids,
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
