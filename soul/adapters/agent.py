from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from soul.services.state import (
    append_patch_proposal,
    load_state,
    load_state_markdown,
    propose_patch,
)
from soul.storage.database import dumps_json, project_scope_id, require_lastrowid


class SoulAgentAdapter:
    """Minimal adapter used by agents before and after task execution."""

    def __init__(self, conn: sqlite3.Connection, project_dir: Path | None = None) -> None:
        self.conn = conn
        self.project_dir = project_dir

    def before_task(self, task: str, limit: int = 10) -> dict[str, Any]:
        state = load_state(self.project_dir)
        return {
            "task": task,
            "state": state,
            "context": load_state_markdown(self.project_dir, limit=limit, task=task),
            "state_artifact": ".soul/state/STATE.md",
        }

    def after_task(
        self,
        task: str,
        outcome: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope_id = project_scope_id(self.conn)
        evidence_payload = {
            "task": task,
            "summary": outcome,
            **(evidence or {}),
        }
        cursor = self.conn.execute(
            """
            INSERT INTO episodes (scope_id, source, summary, content_json, metadata_json)
            VALUES (?, 'agent', ?, ?, ?)
            """,
            (
                scope_id,
                outcome[:120],
                dumps_json(
                    [
                        {"role": "user", "text": task},
                        {"role": "assistant", "text": outcome},
                    ]
                ),
                dumps_json({"adapter": "SoulAgentAdapter", "evidence": evidence_payload}),
            ),
        )
        episode_id = require_lastrowid(cursor)
        evidence_payload["episode_id"] = episode_id
        state = load_state(self.project_dir)
        proposal = propose_patch(state, evidence_payload, source=f"agent:episode:{episode_id}")
        append_patch_proposal(proposal, self.project_dir)
        self.conn.commit()
        return {
            "episode_id": episode_id,
            "evidence": evidence_payload,
            "patch_proposal": proposal,
        }
