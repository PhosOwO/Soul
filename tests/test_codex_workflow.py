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
    runs = (tmp_path / ".soul" / "state" / "integration_runs.jsonl").read_text(encoding="utf-8")
    assert '"host": "codex:cli"' in runs
    assert '"operation": "codex_ingest"' in runs


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


def test_codex_user_install_uses_codex_home_without_hardcoded_home(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    codex_home = tmp_path / "codex-home"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "codex",
            "install",
            "--scope",
            "user",
            "--project-dir",
            str(project),
            "--init",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env={**cli_env(), "CODEX_HOME": str(codex_home), "PATH": ""},
    )

    config = codex_home / "config.toml"
    assert "Installed Soul Codex user config" in result.stdout
    assert config.is_file()
    text = config.read_text(encoding="utf-8")
    assert "[mcp_servers.soul]" in text
    assert "soul-mcp.js" in text
    assert str(project) in text
    assert (project / ".soul" / "state" / "state.json").is_file()
    assert (project / ".soul" / "state" / "STATE.md").is_file()
    assert not (project / ".soul" / "state" / "soul.db").exists()


def test_codex_user_install_preserves_existing_unmanaged_mcp(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    config = tmp_path / "codex-home" / "config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.soul]\ncommand = "custom"\n', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "codex",
            "install",
            "--project-dir",
            str(project),
            "--codex-config",
            str(config),
            "--skip-reme-check",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=cli_env(),
    )

    text = config.read_text(encoding="utf-8")
    assert 'command = "custom"' in text
    assert text.count("[mcp_servers.soul]") == 1
    assert "Skipped existing unmanaged entries" in result.stdout


def test_codex_doctor_reports_cli_ingest_without_mcp_heartbeat(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    write_session(session_path, "I need to complete MHW category evaluation.")

    run_cli(tmp_path, "init", "--project-name", "Test Project")
    run_cli(tmp_path, "codex", "ingest", str(session_path))
    codex_config = tmp_path / "codex-home" / "config.toml"
    codex_config.parent.mkdir()
    codex_config.write_text('[mcp_servers.soul]\ncommand = "node"\n', encoding="utf-8")
    result = run_cli(tmp_path, "codex", "doctor", "--project-dir", str(tmp_path), "--codex-config", str(codex_config))

    assert "Codex Soul doctor:" in result.stdout
    assert "user MCP: configured" in result.stdout
    assert "Codex CLI ingest: present" in result.stdout
    assert "no recent Codex MCP execution heartbeat" in result.stdout


def test_dsh_doctor_reports_recent_http_runs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".soul" / "state"
    state_dir.mkdir(parents=True)
    state_dir.joinpath("integration_runs.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "created_at": "2026-08-17T00:00:00+00:00",
                        "host": "deepseek-harness",
                        "operation": "get_state",
                        "status": "success",
                        "injected": True,
                    }
                ),
                json.dumps(
                    {
                        "created_at": "2026-08-17T00:01:00+00:00",
                        "host": "deepseek-harness:reme",
                        "operation": "propose_reme_transition",
                        "status": "success",
                        "patch_id": "patch-1",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_cli(tmp_path, "dsh", "doctor", "--project-dir", str(tmp_path), "--api-url", "http://127.0.0.1:9")

    assert "DeepSeek Harness Soul doctor:" in result.stdout
    assert "DSH before-turn/state:" in result.stdout
    assert "DSH after-turn/evidence:" in result.stdout
    assert "DeepSeek Harness has executed Soul recently" in result.stdout


def test_init_defaults_project_name_to_current_directory(tmp_path: Path) -> None:
    project = tmp_path / "lark-harmony"
    project.mkdir()

    run_cli(project, "init")

    state_json = json.loads((project / ".soul" / "state" / "state.json").read_text(encoding="utf-8"))
    state_markdown = (project / ".soul" / "state" / "STATE.md").read_text(encoding="utf-8")

    assert state_json["project"] == "lark-harmony"
    assert "Project: lark-harmony" in state_markdown
    assert not (project / ".soul" / "state" / "soul.db").exists()
