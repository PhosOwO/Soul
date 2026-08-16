from __future__ import annotations

import sqlite3

from soul.services.state import format_state_context, load_state


def build_context(conn: sqlite3.Connection | None = None, limit: int = 10) -> str:
    return format_state_context(load_state(), limit=limit)
