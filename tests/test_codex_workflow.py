from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def write_session(path: Path, text: str) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"session_id": "s1"}}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )


def cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "soul.cli", *args],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=cli_env(),
    )


def test_codex_ingest_imports_and_reflects_session(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    write_session(session_path, "I need to complete MHW category evaluation.")

    run_cli(tmp_path, "init", "--project-name", "Test Project")
    result = run_cli(tmp_path, "codex", "ingest", str(session_path))
    episodes = run_cli(tmp_path, "episode", "list")

    assert "Imported Codex episode: 1" in result.stdout
    assert "Patch proposals: none" in result.stdout
    assert "codex" in episodes.stdout


def test_codex_ingest_updates_existing_episode_without_duplicate_patch(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    write_session(session_path, "I need to complete MHW category evaluation.")

    run_cli(tmp_path, "init", "--project-name", "Test Project")
    first = run_cli(tmp_path, "codex", "ingest", str(session_path))
    second = run_cli(tmp_path, "codex", "ingest", str(session_path))

    assert "Imported Codex episode: 1" in first.stdout
    assert "Updated Codex episode: 1" in second.stdout
    assert "Patch proposals: none" in second.stdout


def test_context_command_prints_current_state_only(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    write_session(session_path, "I need to complete MHW category evaluation.")

    run_cli(tmp_path, "init", "--project-name", "Test Project")
    run_cli(tmp_path, "codex", "ingest", str(session_path))
    result = run_cli(tmp_path, "context")

    assert "Soul Current State" in result.stdout
    assert "Project: Test Project" in result.stdout
    assert "Open Candidates:" not in result.stdout
    assert "Legacy Compatibility" not in result.stdout
