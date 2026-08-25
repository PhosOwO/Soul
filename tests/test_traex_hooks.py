from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from shutil import copytree
from typing import Any

from soul.hooks.runtime import append_hook_run, run_stop_hook
from soul.services.state import load_state


ROOT = Path(__file__).resolve().parents[1]


def run_hook(script: str, payload: dict[str, object]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, f".trae/hooks/{script}"],
        cwd=ROOT,
        input=json.dumps(payload),
        env={**cli_env(), "SOUL_DISABLE_BACKGROUND_DRAIN": "1"},
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
        env={**cli_env(), "SOUL_DISABLE_BACKGROUND_DRAIN": "1"},
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
        env={
            **cli_env(),
            "HOME": str(tmp_path / "home"),
            "PATH": "",
            "LLM_API_KEY": "",
            "LLM_BASE_URL": "",
            "LLM_MODEL_NAME": "",
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Installed TraeX Soul integration" in completed.stdout
    assert (project / ".trae" / ".mcp.json").is_file()
    assert (project / ".trae" / "hooks.json").is_file()
    assert (project / ".trae" / "hooks" / "soul_user_prompt_submit.py").is_file()
    assert (project / ".trae" / "hooks" / "soul_stop.py").is_file()
    assert (project / ".soul" / "state" / "state.json").is_file()
    assert (project / ".soul" / "state" / "STATE.md").is_file()
    assert not (project / ".soul" / "state" / "soul.db").exists()
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
    assert "bin/soul.js hook user-prompt-submit --host traex" in text
    assert "bin/soul.js hook stop --host traex" in text
    assert ".trae/hooks/soul_user_prompt_submit.py" not in text
    assert ".trae/hooks/soul_stop.py" not in text
    assert str(project) not in text
    assert "Dynamic project mode" in text
    assert "--project-dir" not in text
    assert (project / ".soul" / "state" / "state.json").is_file()
    assert (project / ".soul" / "state" / "STATE.md").is_file()
    assert not (project / ".soul" / "state" / "soul.db").exists()


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
    assert "bin/soul.js hook user-prompt-submit --host traex" in text
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
                "queued": True,
                "job_id": "turn-1",
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
    assert "Stop success" in completed.stdout
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
    assert "ReMe auto_memory config:" in completed.stdout
    assert "LLM_API_KEY: missing" in completed.stdout
    assert "missing environment for ReMe auto_memory" in completed.stdout


def test_reme_doctor_reads_project_env_for_auto_memory_config(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    (project / ".env").write_text(
        "\n".join(
            [
                "LLM_API_KEY=test-key",
                "LLM_BASE_URL=http://example.test/v1",
                "LLM_MODEL_NAME=test-model",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "reme", "doctor", "--project-dir", str(project)],
        cwd=ROOT,
        env={
            **cli_env(),
            "HOME": str(tmp_path / "home"),
            "PATH": "",
            "LLM_API_KEY": "",
            "LLM_BASE_URL": "",
            "LLM_MODEL_NAME": "",
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "ReMe auto_memory config:" in completed.stdout
    assert f"env file: {project / '.env'}" in completed.stdout
    assert "LLM_API_KEY: set (.env " in completed.stdout
    assert "LLM_BASE_URL: set (.env " in completed.stdout
    assert "LLM_MODEL_NAME: set (.env " in completed.stdout
    assert "status: ok" in completed.stdout


def test_reme_init_config_creates_global_template(tmp_path: Path) -> None:
    soul_home = tmp_path / "soul-home"

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "reme", "init-config"],
        cwd=ROOT,
        env={**cli_env(), "SOUL_HOME": str(soul_home)},
        text=True,
        capture_output=True,
        check=True,
    )

    config = soul_home / ".env"
    assert "Created ReMe auto_memory config template" in completed.stdout
    assert str(config) in completed.stdout
    text = config.read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-your-api-key" in text
    assert "LLM_BASE_URL=https://api.deepseek.com/v1" in text
    assert "LLM_MODEL_NAME=deepseek-chat" in text
    assert "# LLM_BASE_URL=https://ark-cn-beijing.bytedance.net/api/v3" in text


def test_reme_init_config_creates_project_template(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "reme",
            "init-config",
            "--scope",
            "project",
            "--project-dir",
            str(project),
        ],
        cwd=ROOT,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    config = project / ".env"
    assert "Created ReMe auto_memory config template" in completed.stdout
    assert str(config) in completed.stdout
    assert "LLM_MODEL_NAME=deepseek-chat" in config.read_text(encoding="utf-8")


def test_reme_doctor_reports_stopped_service_without_failing(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    reme = fake_bin / "reme"
    reme.write_text("#!/bin/sh\necho 'reme not started. Try: reme start' >&2\nexit 1\n", encoding="utf-8")
    reme.chmod(0o755)

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "reme", "doctor", "--project-dir", str(tmp_path)],
        cwd=ROOT,
        env={
            **cli_env(),
            "PATH": str(fake_bin),
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "http://example.test/v1",
            "LLM_MODEL_NAME": "test-model",
        },
        text=True,
        capture_output=True,
        check=True,
    )

    assert "ReMe preflight:" in completed.stdout
    assert "cli: " in completed.stdout
    assert "service: not available" in completed.stdout
    assert "reme start" in completed.stdout
    assert "ReMe auto_memory config:" in completed.stdout
    assert "LLM_API_KEY: set" in completed.stdout
    assert "LLM_BASE_URL: set" in completed.stdout
    assert "LLM_MODEL_NAME: set" in completed.stdout


def test_reme_doctor_warns_when_workspace_files_are_at_project_root(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    reme = fake_bin / "reme"
    reme.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    reme.chmod(0o755)
    project = tmp_path / "consumer"
    project.mkdir()
    (project / ".soul" / "reme").mkdir(parents=True)
    (project / "daily").mkdir()
    (project / "daily" / "2026-08-18.md").write_text("misplaced\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "reme", "doctor", "--project-dir", str(project)],
        cwd=ROOT,
        env={**cli_env(), "PATH": str(fake_bin)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "ReMe files appear to be written at the project root" in completed.stdout
    assert "found: daily" in completed.stdout


def test_state_review_apply_reject_and_edit_are_user_readable(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "state",
            "diff",
            "--summary",
            "ERA5 accumulated-like heat flux files should be converted to W/m2 by dividing by 3600.",
        ],
        cwd=project,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    proposal = json.loads((project / ".soul" / "state" / "patch_proposals.jsonl").read_text(encoding="utf-8").splitlines()[0])

    review = subprocess.run(
        [sys.executable, "-m", "soul.cli", "state", "review", proposal["id"]],
        cwd=project,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Knowledge Points:" in review.stdout
    assert "converted to W/m2" in review.stdout
    assert "Refs:" not in review.stdout

    apply = subprocess.run(
        [sys.executable, "-m", "soul.cli", "state", "apply", proposal["id"], "--confirmed-by", "test"],
        cwd=project,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Applied State Patch" in apply.stdout
    state_text = (project / ".soul" / "state" / "STATE.md").read_text(encoding="utf-8")
    assert "converted to W/m2" in state_text

    subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "state",
            "diff",
            "--summary",
            "Created a one-off script and printed temporary validation output.",
        ],
        cwd=project,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    reject_id = json.loads((project / ".soul" / "state" / "patch_proposals.jsonl").read_text(encoding="utf-8").splitlines()[-1])["id"]
    reject = subprocess.run(
        [sys.executable, "-m", "soul.cli", "state", "reject", reject_id, "--reason", "episodic"],
        cwd=project,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Rejected State Patch" in reject.stdout
    assert '"status": "rejected"' in (project / ".soul" / "state" / "patch_proposals.jsonl").read_text(encoding="utf-8")

    edit = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "state",
            "edit",
            reject_id,
            "--title",
            "Manual project rule",
            "--knowledge-point",
            "Do not overwrite existing base preprocessing outputs by default.",
            "--why-remember",
            "This protects reusable project outputs.",
        ],
        cwd=project,
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Manual project rule" in edit.stdout
    assert "Do not overwrite" in edit.stdout


def test_reme_start_uses_project_workspace(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "reme-args.txt"
    if os.name == "nt":
        reme = fake_bin / "reme.cmd"
        reme.write_text("@echo off\r\necho %*>\"%REME_ARG_LOG%\"\r\nexit /b 0\r\n", encoding="utf-8")
    else:
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
            "--foreground",
        ],
        cwd=ROOT,
        env={**cli_env(), "PATH": str(fake_bin), "REME_ARG_LOG": str(log_path)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.returncode == 0
    args = log_path.read_text(encoding="utf-8")
    assert "start" in args
    assert f"workspace_dir={project / '.soul' / 'reme'}" in args
    assert "service.host=127.0.0.1" in args
    assert "service.port=2333" in args
    assert "service.show_metadata" not in args


def test_traex_user_prompt_submit_hook_injects_soul_state() -> None:
    heartbeat = ROOT / ".soul" / "state" / "hook_runs.jsonl"
    before = heartbeat.read_text(encoding="utf-8").splitlines() if heartbeat.exists() else []
    load_state(ROOT, project_name="SoulKit")

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
    queue_path = ROOT / ".soul" / "state" / "queue" / "jobs.jsonl"
    queue_before = queue_path.read_text(encoding="utf-8").splitlines() if queue_path.exists() else []
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
    assert "Soul queued evidence job" in str(output["systemMessage"])
    after = heartbeat.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in after[len(before):]]
    record = next(record for record in records if record.get("session_id") == session_id)
    assert record["hook_event_name"] == "Stop"
    assert record["status"] == "success"
    assert record["queued"] is True
    assert record["job_id"]
    assert record["background_drain_started"] is False
    queue_after = queue_path.read_text(encoding="utf-8").splitlines()
    jobs = [json.loads(line) for line in queue_after[len(queue_before):]]
    job = next(job for job in jobs if job.get("session_id") == session_id)
    assert job["type"] == "turn_evidence"
    assert job["payload"]["outcome"] == "This should become evidence."


def test_generic_hook_cli_records_evidence_without_trae_template(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    env = {**cli_env(), "SOUL_DISABLE_BACKGROUND_DRAIN": "1"}
    payload = {
        "cwd": str(project),
        "prompt": "Capture a durable rule",
        "last_assistant_message": "Use the shared hook runtime for host adapters.",
        "session_id": "generic-hook-session",
        "hook_event_name": "Stop",
    }

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "hook", "stop", "--host", "codex"],
        cwd=ROOT,
        input=json.dumps(payload),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    output = json.loads(completed.stdout)
    assert "Soul queued evidence job" in output["systemMessage"]
    hook_runs = (project / ".soul" / "state" / "hook_runs.jsonl").read_text(encoding="utf-8").splitlines()
    heartbeat = json.loads(hook_runs[-1])
    assert heartbeat["host"] == "codex"
    assert heartbeat["queued"] is True
    jobs = (project / ".soul" / "state" / "queue" / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
    job = json.loads(jobs[-1])
    assert job["source"] == "codex"
    assert job["payload"]["outcome"] == "Use the shared hook runtime for host adapters."


def test_hook_heartbeat_write_failure_is_reported(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    monkeypatch.setenv("SOUL_DISABLE_BACKGROUND_DRAIN", "1")

    def fail_hook_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "host": "traex",
            "queued": True,
            "heartbeat_written": False,
            "heartbeat_error": "permission denied",
        }

    monkeypatch.setattr("soul.hooks.runtime.append_hook_run", fail_hook_run)

    result = run_stop_hook(
        {
            "cwd": str(project),
            "prompt": "Remember this",
            "last_assistant_message": "This should become evidence.",
            "session_id": "heartbeat-failure-session",
            "hook_event_name": "Stop",
        },
        host="traex",
    )

    assert result.heartbeat["heartbeat_written"] is False
    assert "Soul queued evidence job" in result.output["systemMessage"]
    assert "Soul hook heartbeat was not written: permission denied" in result.output["systemMessage"]
    assert result.output["suppressOutput"] is True


def test_stop_hook_uses_thread_name_as_stable_session_fallback(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    monkeypatch.setenv("SOUL_DISABLE_BACKGROUND_DRAIN", "1")

    result = run_stop_hook(
        {
            "cwd": str(project),
            "prompt": "Remember this",
            "last_assistant_message": "This should become evidence.",
            "thread_name": "Queue hardening",
            "hook_event_name": "Stop",
        },
        host="traex",
    )

    jobs = (project / ".soul" / "state" / "queue" / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
    job = json.loads(jobs[-1])

    assert result.heartbeat["session_id"].startswith("session-")
    assert job["session_id"].startswith("session-")
    assert job["session_id"] != "traex-session"


def test_append_hook_run_reports_write_failure(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    (project / ".soul").write_text("not a directory", encoding="utf-8")

    record = append_hook_run(
        project,
        {"hook_event_name": "Stop"},
        {"status": "success", "host": "traex"},
        default_event="Stop",
    )

    assert record["heartbeat_written"] is False
    assert "heartbeat_error" in record


def test_traex_hooks_load_soul_from_npm_package_layout(tmp_path: Path) -> None:
    project = tmp_path / "consumer"
    project.mkdir()
    copytree(ROOT / ".trae", project / ".trae")
    package_root = project / "node_modules" / "@soulkit" / "soul"
    copytree(ROOT / "soul", package_root / "soul")

    load_state(project, project_name="Consumer")

    output = run_hook_from_cwd(
        project,
        "soul_user_prompt_submit.py",
        {"cwd": str(project), "prompt": "Use npm package layout", "hook_event_name": "UserPromptSubmit"},
    )

    hook_output = output["hookSpecificOutput"]
    assert isinstance(hook_output, dict)
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    assert "Soul Current State" in str(hook_output["additionalContext"])
