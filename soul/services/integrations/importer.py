from __future__ import annotations

from pathlib import Path

from soul.adapters.codex import CodexImportResult, parse_codex_jsonl
from soul.services.integrations.episodes import append_episode, append_system_event, next_episode_id


def import_codex_session(path: Path, project_dir: Path | None = None) -> int:
    result = parse_codex_jsonl(path)
    return save_codex_episode(result, project_dir=project_dir)["id"]


def save_codex_episode(result: CodexImportResult, project_dir: Path | None = None) -> dict:
    episode_id = next_episode_id(project_dir)
    episode = append_episode(project_dir, {**codex_episode_payload(result), "id": episode_id})
    append_system_event(
        project_dir,
        event_type="episode_imported",
        data={"episode_id": episode_id, "source": "codex"},
        reason=f"Imported Codex episode: {result.summary}",
        source="soul import codex",
    )
    return episode


def codex_episode_payload(result: CodexImportResult) -> dict:
    return {
        "source": "codex",
        "source_path": result.source_path,
        "summary": result.summary,
        "messages": result.messages,
        "metadata": {
            "session_id": result.session_id,
            "cwd": result.cwd,
            "originator": result.originator,
            "message_count": len(result.messages),
            "skipped_messages": result.skipped_messages,
        },
    }
