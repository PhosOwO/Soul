from __future__ import annotations

import subprocess

import pytest

from soul.adapters.reme import ReMeCliAdapter, extract_reme_paths, reme_write_refs
from soul.services.reme.runtime_config import resolve_reme_runtime_config


def test_reme_preflight_requires_cli(tmp_path, monkeypatch):
    monkeypatch.setattr("soul.adapters.reme.shutil.which", lambda command: None)

    adapter = ReMeCliAdapter(tmp_path)

    with pytest.raises(RuntimeError, match="ReMe CLI is not available"):
        adapter.preflight()

    result = adapter.check_preflight()
    assert not result.ok
    assert result.cli_path is None
    assert result.workspace_dir == tmp_path / ".soul" / "reme"


def test_reme_preflight_creates_default_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("soul.adapters.reme.shutil.which", lambda command: "/usr/local/bin/reme")

    adapter = ReMeCliAdapter(tmp_path)
    adapter.check_preflight(create_workspace=True)

    assert (tmp_path / ".soul" / "reme").is_dir()

    result = adapter.check_preflight()
    assert result.ok
    assert result.cli_path == "/usr/local/bin/reme"


def test_reme_preflight_reports_stopped_service(tmp_path, monkeypatch):
    monkeypatch.setattr("soul.adapters.reme.shutil.which", lambda command: "/usr/local/bin/reme")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="reme not started. Try: reme start")

    monkeypatch.setattr("soul.adapters.reme.subprocess.run", fake_run)

    adapter = ReMeCliAdapter(tmp_path)
    result = adapter.check_preflight(create_workspace=True, check_service=True)

    assert not result.ok
    assert result.cli_path == "/usr/local/bin/reme"
    assert result.service_ok is False
    assert "ReMe service is not available" in result.message
    assert "reme start" in result.message

    with pytest.raises(RuntimeError, match="ReMe service is not available"):
        adapter.preflight(check_service=True)


def test_reme_command_uses_local_job_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("soul.adapters.reme.shutil.which", lambda command: "/usr/local/bin/reme")
    commands = []
    captured_env = {}

    def fake_run(command, **kwargs):
        commands.append(command)
        captured_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(args=command, returncode=0, stdout='ok\n{"path":"daily/a.md"}\n', stderr="")

    monkeypatch.setattr("soul.adapters.reme.subprocess.run", fake_run)
    (tmp_path / ".env").write_text(
        "LLM_API_KEY=test-key\nLLM_BASE_URL=http://example.test/v1\nLLM_MODEL_NAME=test-model\n",
        encoding="utf-8",
    )

    result = ReMeCliAdapter(tmp_path).auto_memory(
        session_id="s1",
        messages=[{"name": "user", "role": "user", "content": "hello"}],
    )

    assert result.metadata == {"path": "daily/a.md"}
    assert commands[0][:3] == ["reme", "start", "job=auto_memory"]
    assert captured_env["LLM_API_KEY"] == "test-key"
    assert captured_env["LLM_BASE_URL"] == "http://example.test/v1"
    assert captured_env["LLM_MODEL_NAME"] == "test-model"


def test_reme_start_service_uses_server_safe_args(tmp_path, monkeypatch):
    monkeypatch.setattr("soul.adapters.reme.shutil.which", lambda command: "/usr/local/bin/reme")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("soul.adapters.reme.subprocess.run", fake_run)

    code = ReMeCliAdapter(tmp_path).start_service(host="127.0.0.1", port=2333)

    assert code == 0
    command = commands[0]
    assert command[:2] == ["reme", "start"]
    assert "service.host=127.0.0.1" in command
    assert "service.port=2333" in command
    assert "service.show_metadata=true" not in command


def test_reme_runtime_config_infers_openai_provider_from_host_config(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    config = tmp_path / ".trae" / "traecli.toml"
    config.parent.mkdir()
    config.write_text(
        "\n".join(
            [
                'model = "coding"',
                'model_provider = "custom"',
                "",
                "[[models]]",
                'name = "coding"',
                'context_window = 128000',
                'harness_mode = "codex"',
                "",
                "[models.open_ai]",
                'base_url = "https://ark.example/api/v3"',
                'api_key = "${ARK_API_KEY}"',
                'model = "ep-test"',
            ]
        ),
        encoding="utf-8",
    )

    runtime_config = resolve_reme_runtime_config(tmp_path)

    assert runtime_config.values["LLM_API_KEY"] == "ark-key"
    assert runtime_config.values["LLM_BASE_URL"] == "https://ark.example/api/v3"
    assert runtime_config.values["LLM_MODEL_NAME"] == "ep-test"
    assert "host config" in runtime_config.sources["LLM_API_KEY"]


def test_reme_runtime_config_uses_global_env_and_project_override(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.delenv("SOUL_HOME", raising=False)
    home = tmp_path / "home"
    global_env = home / ".soul" / ".env"
    global_env.parent.mkdir(parents=True)
    global_env.write_text(
        "LLM_API_KEY=global-key\nLLM_BASE_URL=https://global.example/v1\nLLM_MODEL_NAME=global-model\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("LLM_MODEL_NAME=project-model\n", encoding="utf-8")

    runtime_config = resolve_reme_runtime_config(project, home_dir=home)

    assert runtime_config.values["LLM_API_KEY"] == "global-key"
    assert runtime_config.values["LLM_BASE_URL"] == "https://global.example/v1"
    assert runtime_config.values["LLM_MODEL_NAME"] == "project-model"
    assert runtime_config.env_files == [global_env, project / ".env"]


def test_reme_runtime_config_uses_soul_home_env(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    soul_home = tmp_path / "custom-soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    global_env = soul_home / ".env"
    global_env.parent.mkdir(parents=True)
    global_env.write_text(
        "LLM_API_KEY=soul-home-key\nLLM_BASE_URL=https://soul-home.example/v1\nLLM_MODEL_NAME=soul-home-model\n",
        encoding="utf-8",
    )

    runtime_config = resolve_reme_runtime_config(tmp_path / "project")

    assert runtime_config.values["LLM_API_KEY"] == "soul-home-key"
    assert runtime_config.values["LLM_BASE_URL"] == "https://soul-home.example/v1"
    assert runtime_config.values["LLM_MODEL_NAME"] == "soul-home-model"
    assert runtime_config.env_files == [global_env]


def test_reme_write_refs_extracts_nested_paths():
    metadata = {
        "path": "daily\\2026-08-17\\a.md",
        "source_conversation": "[[session/dialog/s1.jsonl]]",
        "cards": [{"daily_path": "daily/2026-08-17/b.md"}],
    }

    assert extract_reme_paths(metadata) == {
        "daily/2026-08-17/a.md",
        "daily/2026-08-17/b.md",
        "session/dialog/s1.jsonl",
    }
    assert reme_write_refs(metadata) == [
        {"type": "reme_file", "path": "daily/2026-08-17/a.md"},
        {"type": "reme_file", "path": "daily/2026-08-17/b.md"},
        {"type": "reme_file", "path": "session/dialog/s1.jsonl"},
    ]
