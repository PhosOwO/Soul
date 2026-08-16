from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_SOUL_DIR = ".brain"
DEFAULT_DB_NAME = "soul.db"


def default_db_path(project_dir: Path | None = None) -> Path:
    root = project_dir or Path.cwd()
    return root / DEFAULT_SOUL_DIR / DEFAULT_DB_NAME


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def dumps_json(value: dict[str, Any] | list[Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def init_database(conn: sqlite3.Connection, project_name: str = "Soul Project") -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scopes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER REFERENCES scopes(id),
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'project',
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id INTEGER NOT NULL REFERENCES scopes(id),
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'accepted',
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id INTEGER NOT NULL REFERENCES scopes(id),
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'temp',
            source TEXT,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id INTEGER NOT NULL REFERENCES scopes(id),
            source TEXT NOT NULL,
            source_path TEXT,
            summary TEXT NOT NULL,
            content_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER REFERENCES entities(id),
            type TEXT NOT NULL,
            change_json TEXT NOT NULL DEFAULT '{}',
            reason TEXT,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS cognitive_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER REFERENCES entities(id),
            candidate_id INTEGER REFERENCES candidates(id),
            type TEXT NOT NULL,
            change_json TEXT NOT NULL DEFAULT '{}',
            reason TEXT,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}',
            reason TEXT,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id INTEGER NOT NULL REFERENCES scopes(id),
            path TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'file',
            source_episode_id INTEGER REFERENCES episodes(id),
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope_id, path)
        );

        CREATE TABLE IF NOT EXISTS entity_artifacts (
            entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'practiced_in',
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_id, artifact_id, relation)
        );
        """
    )

    row = conn.execute("SELECT id FROM scopes WHERE parent_id IS NULL LIMIT 1").fetchone()
    if row is None:
        cursor = conn.execute(
            "INSERT INTO scopes (name, type, data_json) VALUES (?, 'project', ?)",
            (project_name, dumps_json({"initialized_by": "soul init"})),
        )
        scope_id = cursor.lastrowid
        entity_cursor = conn.execute(
            """
            INSERT INTO entities (scope_id, type, name, state, data_json)
            VALUES (?, 'Project', ?, 'initialized', ?)
            """,
            (scope_id, project_name, dumps_json({})),
        )
        conn.execute(
            """
            INSERT INTO cognitive_events (entity_id, type, change_json, reason, source)
            VALUES (?, 'project_initialized', ?, 'Soul project initialized', 'soul init')
            """,
            (entity_cursor.lastrowid, dumps_json({"state": "initialized"})),
        )
    migrate_legacy_events(conn)
    clean_obvious_task_cognition(conn)
    conn.commit()


def project_scope_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM scopes WHERE parent_id IS NULL ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("Soul database is not initialized. Run `soul init` first.")
    return int(row["id"])


SYSTEM_EVENT_TYPES = {"episode_imported", "episode_updated", "episode_reflected"}
COGNITIVE_EVENT_TYPES = {"project_initialized", "candidate_promoted", "entity_state_changed"}


def migrate_legacy_events(conn: sqlite3.Connection) -> None:
    legacy_events = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    for event in legacy_events:
        if event["type"] in SYSTEM_EVENT_TYPES:
            exists = conn.execute(
                """
                SELECT 1 FROM system_events
                WHERE type = ? AND data_json = ? AND source IS ? AND created_at = ?
                """,
                (event["type"], event["change_json"], event["source"], event["created_at"]),
            ).fetchone()
            if exists is None:
                conn.execute(
                    """
                    INSERT INTO system_events (type, data_json, reason, source, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (event["type"], event["change_json"], event["reason"], event["source"], event["created_at"]),
                )
        elif event["type"] in COGNITIVE_EVENT_TYPES:
            exists = conn.execute(
                """
                SELECT 1 FROM cognitive_events
                WHERE type = ? AND entity_id IS ? AND change_json = ? AND source IS ? AND created_at = ?
                """,
                (
                    event["type"],
                    event["entity_id"],
                    event["change_json"],
                    event["source"],
                    event["created_at"],
                ),
            ).fetchone()
            if exists is None:
                conn.execute(
                    """
                    INSERT INTO cognitive_events (entity_id, type, change_json, reason, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["entity_id"],
                        event["type"],
                        event["change_json"],
                        event["reason"],
                        event["source"],
                        event["created_at"],
                    ),
                )


def clean_obvious_task_cognition(conn: sqlite3.Connection) -> None:
    # Legacy cleanup is intentionally non-destructive. Earlier versions tried to
    # identify task pollution by natural-language markers, which is brittle and
    # language-specific. Explicit migration tools should handle any future data
    # cleanup with user review.
    return
