from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from shutil import copytree

from soul.storage.database import connect, default_db_path, init_database


ROOT = Path(__file__).resolve().parents[1]


def run_hook(script: str, payload: dict[str, object]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, f".trae/hooks/{script}"],
        cwd=ROOT,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def run_hook_from_cwd(cwd: Path, script: str, payload: dict[str, object]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, f".trae/hooks/{script}"],
        cwd=cwd,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_traex_install_copies_templates_and_initializes_project(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "traex",
            "install",
            "--project-dir",
            str(project),
            "--init",
            "--project-name",
            "Consumer",
        ],
        cwd=ROOT,
        env={**cli_env(), "PATH": ""},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Installed TraeX Soul integration" in completed.stdout
    assert (project / ".trae" / ".mcp.json").is_file()
    assert (project / ".trae" / "hooks.json").is_file()
    assert (project / ".trae" / "hooks" / "soul_user_prompt_submit.py").is_file()
    assert (project / ".trae" / "hooks" / "soul_stop.py").is_file()
    assert default_db_path(project).is_file()
    assert "ReMe preflight:" in completed.stdout
    assert "cli: not found" in completed.stdout


def test_traex_install_does_not_overwrite_without_force(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    config = project / ".trae" / ".mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"custom": true}\n', encoding="utf-8")

    subprocess.run(
        [sys.executable, "-m", "soul.cli", "traex", "install", "--project-dir", str(project)],
        cwd=ROOT,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    assert config.read_text(encoding="utf-8") == '{"custom": true}\n'


def test_traex_user_install_uses_trae_home_without_hardcoded_home(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    trae_home = tmp_path / "trae-home"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "traex",
            "install",
            "--scope",
            "user",
            "--project-dir",
            str(project),
            "--init",
        ],
        cwd=ROOT,
        env={**cli_env(), "TRAE_HOME": str(trae_home), "PATH": ""},
        text=True,
        capture_output=True,
        check=True,
    )

    config = trae_home / "traecli.toml"
    assert "Installed Soul TraeX user config" in completed.stdout
    assert config.is_file()
    text = config.read_text(encoding="utf-8")
    assert "[mcp_servers.soul]" in text
    assert "[[hooks.UserPromptSubmit.hooks]]" in text
    assert "[[hooks.Stop.hooks]]" in text
    assert str(project) in text
    assert default_db_path(project).is_file()


def test_traex_user_install_preserves_existing_unmanaged_mcp(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    config = tmp_path / "custom" / "traecli.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.soul]\ncommand = "custom"\n', encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "traex",
            "install",
            "--scope",
            "user",
            "--project-dir",
            str(project),
            "--traex-config",
            str(config),
            "--skip-reme-check",
        ],
        cwd=ROOT,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    text = config.read_text(encoding="utf-8")
    assert 'command = "custom"' in text
    assert text.count("[mcp_servers.soul]") == 1
    assert "[[hooks.UserPromptSubmit.hooks]]" in text
    assert "Skipped existing unmanaged entries" in completed.stdout


def test_traex_doctor_reports_missing_heartbeat(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    (project / ".trae").mkdir()
    (project / ".trae" / ".mcp.json").write_text('{"mcpServers":{"soul":{}}}\n', encoding="utf-8")
    (project / ".trae" / "hooks.json").write_text('{"hooks":{"Stop":[{"hooks":[{"command":"soul_stop"}]}]}}\n', encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "traex", "doctor", "--project-dir", str(project)],
        cwd=ROOT,
        env={**cli_env(), "TRAE_HOME": str(tmp_path / "trae-home")},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "TraeX Soul doctor:" in completed.stdout
    assert "project MCP:" in completed.stdout
    assert "configured status is not enough" in completed.stdout


def test_traex_doctor_reports_recent_heartbeat_and_reme_files(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    hook_dir = project / ".soul" / "state"
    hook_dir.mkdir(parents=True)
    hook_dir.joinpath("hook_runs.jsonl").write_text(
        json.dumps(
            {
                "created_at": "2026-08-17T00:00:00+00:00",
                "hook_event_name": "Stop",
                "status": "success",
                "recorded": True,
                "patch_id": "patch-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (project / ".soul" / "reme" / "daily").mkdir(parents=True)
    (project / ".soul" / "reme" / "daily" / "2026-08-17.md").write_text("memory\n", encoding="utf-8")
    (project / ".soul" / "traces").mkdir(parents=True)
    (project / ".soul" / "traces" / "reme_state_trace.md").write_text("trace\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "traex", "doctor", "--project-dir", str(project)],
        cwd=ROOT,
        env={**cli_env(), "TRAE_HOME": str(tmp_path / "trae-home")},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "hook heartbeat: present" in completed.stdout
    assert "patch=patch-1" in completed.stdout
    assert "ReMe evidence: present" in completed.stdout
    assert "hooks executed recently" in completed.stdout


def test_reme_doctor_reports_missing_cli_without_failing(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "reme", "doctor", "--project-dir", str(tmp_path)],
        cwd=ROOT,
        env={**cli_env(), "PATH": ""},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "ReMe preflight:" in completed.stdout
    assert "cli: not found" in completed.stdout
    assert "install ReMe" in completed.stdout


def test_reme_doctor_reports_stopped_service_without_failing(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    reme = fake_bin / "reme"
    reme.write_text("#!/bin/sh\necho 'reme not started. Try: reme start' >&2\nexit 1\n", encoding="utf-8")
    reme.chmod(0o755)

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "reme", "doctor", "--project-dir", str(tmp_path)],
        cwd=ROOT,
        env={**cli_env(), "PATH": str(fake_bin)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "ReMe preflight:" in completed.stdout
    assert "cli: " in completed.stdout
    assert "service: not available" in completed.stdout
    assert "reme start" in completed.stdout


def test_reme_start_uses_project_workspace(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "reme-args.txt"
    reme = fake_bin / "reme"
    reme.write_text(
        "#!/bin/sh\nprintf '%s\n' \"$@\" > \"$REME_ARG_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    reme.chmod(0o755)
    project = tmp_path / "consumer"
    project.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "reme",
            "start",
            "--project-dir",
            str(project),
            "--host",
            "127.0.0.1",
            "--port",
            "2333",
        ],
        cwd=ROOT,
        env={**cli_env(), "PATH": str(fake_bin), "REME_ARG_LOG": str(log_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.returncode == 0
    args = log_path.read_text(encoding="utf-8")
    assert "start\n" in args
    assert f"workspace_dir={project / '.soul' / 'reme'}" in args
    assert "service.host=127.0.0.1" in args
    assert "service.port=2333" in args
    assert "service.show_metadata" not in args


def test_traex_user_prompt_submit_hook_injects_soul_state() -> None:
    heartbeat = ROOT / ".soul" / "state" / "hook_runs.jsonl"
    before = heartbeat.read_text(encoding="utf-8").splitlines() if heartbeat.exists() else []
    with connect(default_db_path(ROOT)) as conn:
        init_database(conn, project_name="SoulKit")

    turn_id = "test-user-prompt-heartbeat"
    output = run_hook(
        "soul_user_prompt_submit.py",
        {
            "cwd": str(ROOT),
            "prompt": "What should we do next?",
            "hook_event_name": "UserPromptSubmit",
            "turn_id": turn_id,
        },
    )

    hook_output = output["hookSpecificOutput"]
    assert isinstance(hook_output, dict)
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    assert "Soul Current State" in str(hook_output["additionalContext"])
    after = heartbeat.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in after[len(before):]]
    record = next(record for record in records if record.get("turn_id") == turn_id)
    assert record["hook_event_name"] == "UserPromptSubmit"
    assert record["status"] == "success"
    assert record["injected"] is True


def test_traex_stop_hook_records_evidence_without_blocking() -> None:
    heartbeat = ROOT / ".soul" / "state" / "hook_runs.jsonl"
    before = heartbeat.read_text(encoding="utf-8").splitlines() if heartbeat.exists() else []
    session_id = "test-traex-session-heartbeat"
    output = run_hook(
        "soul_stop.py",
        {
            "cwd": str(ROOT),
            "prompt": "Remember this",
            "last_assistant_message": "This should become evidence.",
            "session_id": session_id,
            "hook_event_name": "Stop",
        },
    )

    assert "continue" not in output
    assert "Soul proposed state patch" in str(output["systemMessage"])
    after = heartbeat.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in after[len(before):]]
    record = next(record for record in records if record.get("session_id") == session_id)
    assert record["hook_event_name"] == "Stop"
    assert record["status"] == "success"
    assert record["recorded"] is True
    assert record["patch_id"]


def test_traex_hooks_load_soul_from_npm_package_layout(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    copytree(ROOT / ".trae", project / ".trae")
    package_root = project / "node_modules" / "@soulkit" / "soul"
    copytree(ROOT / "soul", package_root / "soul")

    with connect(default_db_path(project)) as conn:
        init_database(conn, project_name="Consumer")

    output = run_hook_from_cwd(
        project,
        "soul_user_prompt_submit.py",
        {"cwd": str(project), "prompt": "Use npm package layout", "hook_event_name": "UserPromptSubmit"},
    )

    hook_output = output["hookSpecificOutput"]
    assert isinstance(hook_output, dict)
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    assert "Soul Current State" in str(hook_output["additionalContext"])
