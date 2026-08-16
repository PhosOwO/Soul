from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from soul.adapters.codex import CodexImportResult, parse_codex_jsonl
from soul.services.importer import save_codex_episode
from soul.services.reflection import reflect_episode
from soul.storage.database import dumps_json


@dataclass(slots=True)
class CodexIngestResult:
    episode_id: int
    imported: bool
    patch_proposal_ids: list[str]
    summary: str


def ingest_codex_session(conn: sqlite3.Connection, path: Path, max_patches: int = 3) -> CodexIngestResult:
    result = parse_codex_jsonl(path)
    episode_id, imported = upsert_codex_episode(conn, result)
    patch_proposal_ids = reflect_episode(conn, episode_id, max_patches=max_patches)
    return CodexIngestResult(
        episode_id=episode_id,
        imported=imported,
        patch_proposal_ids=patch_proposal_ids,
        summary=result.summary,
    )


def upsert_codex_episode(conn: sqlite3.Connection, result: CodexImportResult) -> tuple[int, bool]:
    existing = conn.execute(
        "SELECT id FROM episodes WHERE source = 'codex' AND source_path = ? ORDER BY id DESC LIMIT 1",
        (result.source_path,),
    ).fetchone()
    if existing is None:
        return save_codex_episode(conn, result), True

    episode_id = int(existing["id"])
    conn.execute(
        """
        UPDATE episodes
        SET summary = ?, content_json = ?, metadata_json = ?
        WHERE id = ?
        """,
        (
            result.summary,
            dumps_json(result.messages),
            dumps_json(
                {
                    "session_id": result.session_id,
                    "cwd": result.cwd,
                    "originator": result.originator,
                    "message_count": len(result.messages),
                    "skipped_messages": result.skipped_messages,
                }
            ),
            episode_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO system_events (type, data_json, reason, source)
        VALUES ('episode_updated', ?, ?, 'soul codex ingest')
        """,
        (
            dumps_json({"episode_id": episode_id, "source": "codex"}),
            f"Updated Codex episode: {result.summary}",
        ),
    )
    conn.commit()
    return episode_id, False
