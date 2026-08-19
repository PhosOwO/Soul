from __future__ import annotations

from pathlib import Path

from soul.services.state import (
    apply_patch_proposal,
    load_state,
    propose_patch,
    save_state,
)
from soul.services.state_core.state_store import load_state_markdown


def test_state_patch_requires_explicit_apply(tmp_path: Path) -> None:
    state = load_state(tmp_path, project_name="Test Project")
    assert (tmp_path / ".soul" / "state" / "STATE.md").read_text(encoding="utf-8").startswith("Soul Current State")

    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "Move Soul away from entity-centric storage to a state-centric loop.",
            "content": "Current State -> Evidence -> Cognitive Diff -> State Patch -> Confirm -> New State.",
            "state_item": {
                "id": "no-entity-candidate-compatibility",
                "kind": "accepted_belief",
                "statement": "Soul does not keep an Entity/Candidate compatibility path in the active architecture.",
                "priority": "high",
                "confidence": 0.9,
            },
        },
    )

    unchanged = load_state(tmp_path)
    next_state = apply_patch_proposal(unchanged, proposal, confirmed_by="test")
    save_state(next_state, tmp_path)
    saved = load_state(tmp_path)

    assert unchanged["version"] == 1
    assert saved["version"] == 2
    assert any(item["id"] == "no-entity-candidate-compatibility" for item in saved["current_state"]["state_items"])


def test_agent_before_task_reads_current_state_only(tmp_path: Path) -> None:
    load_state(tmp_path, project_name="Test Project")
    context = load_state_markdown(tmp_path, task="Fix Soul architecture")

    state = load_state(tmp_path)

    assert "Soul Current State" in context
    assert not (tmp_path / ".soul" / "state" / "soul.db").exists()
    assert not (tmp_path / ".soul" / "state" / "patch_proposals.jsonl").exists()
    assert state["version"] == 1

