from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from soul.services.integrations.sessions import resolve_session_id


def test_resolve_session_id_prefers_explicit_session_id(tmp_path: Path) -> None:
    assert (
        resolve_session_id(
            host="traex",
            project_dir=tmp_path,
            payloads=[{"session_id": "host-session"}, {"thread_id": "thread-session"}],
        )
        == "host-session"
    )


def test_resolve_session_id_uses_thread_like_fields(tmp_path: Path) -> None:
    assert (
        resolve_session_id(
            host="codex",
            project_dir=tmp_path,
            payloads=[{"conversation_id": "conversation-1"}],
        )
        == "conversation-1"
    )


def test_resolve_session_id_hashes_session_names(tmp_path: Path) -> None:
    first = resolve_session_id(
        host="traex",
        project_dir=tmp_path,
        payloads=[{"thread_name": "Fix queue writes"}],
    )
    second = resolve_session_id(
        host="traex",
        project_dir=tmp_path,
        payloads=[{"thread_name": "Fix queue writes"}],
    )

    assert first == second
    assert first.startswith("session-")


def test_resolve_session_id_fallback_is_project_host_day_scoped(tmp_path: Path) -> None:
    session_id = resolve_session_id(
        host="codex:mcp",
        project_dir=tmp_path,
        payloads=[{}],
        now=datetime(2026, 8, 19, tzinfo=UTC),
    )

    assert session_id.startswith("soul-codex:mcp-")
    assert session_id.endswith("-20260819")
