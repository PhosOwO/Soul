from __future__ import annotations

import sqlite3
from pathlib import Path

from soul.adapters.codex import CodexImportResult, parse_codex_jsonl
from soul.storage.database import dumps_json, project_scope_id, require_lastrowid


def import_codex_session(conn: sqlite3.Connection, path: Path) -> int:
    result = parse_codex_jsonl(path)
    return save_codex_episode(conn, result)


def save_codex_episode(conn: sqlite3.Connection, result: CodexImportResult) -> int:
    scope_id = project_scope_id(conn)
    cursor = conn.execute(
        """
        INSERT INTO episodes (
            scope_id, source, source_path, summary, content_json, metadata_json
        )
        VALUES (?, 'codex', ?, ?, ?, ?)
        """,
        (
            scope_id,
            result.source_path,
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
        ),
    )
    episode_id = require_lastrowid(cursor)
    conn.execute(
        """
        INSERT INTO system_events (type, data_json, reason, source)
        VALUES ('episode_imported', ?, ?, 'soul import codex')
        """,
        (
            dumps_json({"episode_id": episode_id, "source": "codex"}),
            f"Imported Codex episode: {result.summary}",
        ),
    )
    conn.commit()
    return episode_id
