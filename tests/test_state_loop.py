from __future__ import annotations

from pathlib import Path

from soul.adapters.agent import SoulAgentAdapter
from soul.services.state import (
    apply_patch_proposal,
    load_state,
    propose_patch,
    save_state,
)
from soul.storage.database import connect, init_database


def test_state_patch_requires_explicit_apply(tmp_path: Path) -> None:
    state = load_state(tmp_path, project_name="Test Project")
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


def test_agent_adapter_after_task_records_episode_and_patch_only(tmp_path: Path) -> None:
    db_path = tmp_path / ".soul" / "state" / "soul.db"
    with connect(db_path) as conn:
        init_database(conn, project_name="Test Project")
        adapter = SoulAgentAdapter(conn, project_dir=tmp_path)
        before = adapter.before_task("Fix Soul architecture")
        result = adapter.after_task(
            "Fix Soul architecture",
            "Implemented state-centric Current State -> Evidence -> Cognitive Diff -> State Patch flow.",
        )
        episode = conn.execute("SELECT * FROM episodes WHERE id = ?", (result["episode_id"],)).fetchone()

    state = load_state(tmp_path)

    assert "Soul Current State" in before["context"]
    assert episode is not None
    assert result["patch_proposal"]["status"] == "proposed"
    assert state["version"] == 1

