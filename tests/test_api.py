from __future__ import annotations

from soul.api import SoulApi
from soul.storage.database import connect, default_db_path, init_database


def test_soul_api_get_state_returns_injection(tmp_path):
    with connect(default_db_path(tmp_path)) as conn:
        init_database(conn, project_name="Demo")

    payload = SoulApi(tmp_path).get_state(task="下一步怎么做？")

    assert "Soul Current State" in payload["context"]
    assert "[Soul Current State]" in payload["injection"]
    assert payload["task"] == "下一步怎么做？"


def test_soul_api_propose_transition_records_episode_and_patch(tmp_path):
    with connect(default_db_path(tmp_path)) as conn:
        init_database(conn, project_name="Demo")

    payload = SoulApi(tmp_path).propose_transition(
        evidence={"task": "Review new evidence", "summary": "A new observation needs review before it changes accepted state."},
        episode={"events": [{"type": "assistant_answer", "durable": True}]},
    )

    assert payload["episode_id"] > 0
    assert payload["patch_proposal"]["status"] == "proposed"
    assert payload["patch_proposal"]["operations"]
