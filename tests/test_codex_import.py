from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from soul.adapters.codex import parse_codex_jsonl
from soul.services.integrations.episodes import find_episode, read_system_events
from soul.services.integrations.importer import import_codex_session


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


def test_parse_codex_jsonl_filters_context_blocks(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    write_jsonl(
        session_path,
        [
            {
                "timestamp": "2026-07-25T00:00:00Z",
                "type": "session_meta",
                "payload": {"session_id": "s1", "cwd": "C:/project", "originator": "codex_work_desktop"},
            },
            {
                "timestamp": "2026-07-25T00:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "# AGENTS.md instructions for C:/project\n\n<INSTRUCTIONS>\ninternal guidance\n</INSTRUCTIONS>",
                        },
                        {
                            "type": "input_text",
                            "text": "<recommended_plugins>\nPlugin A\n\nPlugin B\n</recommended_plugins>",
                        },
                        {"type": "input_text", "text": "<environment_context>\nsecret-ish machine context"},
                        {"type": "input_text", "text": "Please add Codex import."},
                    ],
                },
            },
            {
                "timestamp": "2026-07-25T00:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Implemented."}],
                    "phase": "final_answer",
                },
            },
        ],
    )

    result = parse_codex_jsonl(session_path)

    assert result.session_id == "s1"
    assert result.summary == "Please add Codex import."
    assert [message["role"] for message in result.messages] == ["user", "assistant"]
    assert result.messages[0]["text"] == "Please add Codex import."


def test_import_codex_session_persists_episode(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    write_jsonl(
        session_path,
        [
            {"type": "session_meta", "payload": {"session_id": "s1"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Add Codex Adapter."}],
                },
            },
        ],
    )

    episode_id = import_codex_session(session_path, project_dir=tmp_path)
    episode = find_episode(tmp_path, episode_id)
    events = read_system_events(tmp_path)

    assert episode_id.startswith("episode_")
    assert episode is not None
    assert episode["source"] == "codex"
    assert episode["summary"] == "Add Codex Adapter."
    assert any(event.get("type") == "episode_imported" for event in events)
    assert not (tmp_path / ".soul" / "state" / "soul.db").exists()


def test_episode_show_command_prints_imported_messages(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    write_jsonl(
        session_path,
        [
            {"type": "session_meta", "payload": {"session_id": "s1"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Show this episode."}],
                },
            },
        ],
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

    subprocess.run(
        [sys.executable, "-m", "soul.cli", "init", "--project-name", "Test Project"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        [sys.executable, "-m", "soul.cli", "import", "codex", str(session_path)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    listed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "episode", "list"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    episode_id = listed.stdout.split("\t", 2)[1]
    result = subprocess.run(
        [sys.executable, "-m", "soul.cli", "episode", "show", "1", "--limit", "1"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    uuid_result = subprocess.run(
        [sys.executable, "-m", "soul.cli", "episode", "show", episode_id, "--limit", "1"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "#1\tepisode_" in listed.stdout
    assert "summary: Show this episode." in result.stdout
    assert "summary: Show this episode." in uuid_result.stdout
    assert "[1] user" in result.stdout
    assert "Show this episode." in result.stdout
