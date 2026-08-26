from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from soul.services.project_resolver import (
    find_project_root,
    global_project_dir,
    load_project_registry,
    prune_unavailable_projects,
    register_project,
    register_auto_project,
    resolve_project_dir,
    save_project_registry,
)
from soul.services.state_core.state_store import load_state


def test_resolve_project_dir_finds_parent_soul_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SOUL_HOME", str(home / ".soul"))
    project = tmp_path / "repo"
    nested = project / "packages" / "app"
    nested.mkdir(parents=True)
    load_state(project, project_name="Repo")

    assert find_project_root(nested) == project
    assert resolve_project_dir(cwd=nested) == project


def test_register_project_records_global_registry(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "repo"
    project.mkdir()

    record = register_project(project, project_name="Repo")
    registry = load_project_registry()

    assert record["project_dir"] == str(project.resolve())
    assert registry["projects"][0]["project_name"] == "Repo"
    assert Path(registry["projects"][0]["state_path"]) == project.resolve() / ".soul" / "state" / "state.json"
    assert json.loads((soul_home / "projects.json").read_text(encoding="utf-8"))["projects"][0]["project_dir"] == str(project.resolve())


def test_register_auto_project_skips_non_git_directory(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "plain"
    project.mkdir()

    record = register_auto_project(cwd=project)

    assert record is None
    assert not (project / ".soul").exists()
    assert not (soul_home / "projects.json").exists()


def test_register_auto_project_uses_global_state_for_git_project(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "repo"
    nested = project / "packages" / "app"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()

    record = register_auto_project(cwd=nested)
    assert record is not None

    expected_owner = global_project_dir(str(record["project_id"]))
    assert record["project_dir"] == str(project.resolve())
    assert record["storage"] == "global"
    assert Path(record["state_root"]) == expected_owner / ".soul" / "state"
    assert not (project / ".soul").exists()

    registry = load_project_registry()
    assert registry["projects"][0]["project_id"] == record["project_id"]


def test_prune_unavailable_projects_removes_only_stale_records(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    now = datetime.now(UTC).replace(microsecond=0)
    stale = (now - timedelta(days=31)).isoformat().replace("+00:00", "Z")
    recent = (now - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    save_project_registry(
        {
            "schema_version": 1,
            "projects": [
                {
                    "project_id": "stale",
                    "project_dir": str(tmp_path / "stale"),
                    "project_name": "Stale",
                    "status": "unavailable",
                    "unavailable_since": stale,
                },
                {
                    "project_id": "recent",
                    "project_dir": str(tmp_path / "recent"),
                    "project_name": "Recent",
                    "status": "unavailable",
                    "unavailable_since": recent,
                },
                {
                    "project_id": "active",
                    "project_dir": str(tmp_path / "active"),
                    "project_name": "Active",
                    "status": "active",
                },
            ],
        }
    )

    result = prune_unavailable_projects(unavailable_days=30)

    assert result["removed_count"] == 1
    assert result["removed"][0]["project_id"] == "stale"
    registry = load_project_registry()
    assert [item["project_id"] for item in registry["projects"]] == ["recent", "active"]


def test_projects_prune_cli_json(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    stale = (datetime.now(UTC) - timedelta(days=31)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    save_project_registry(
        {
            "schema_version": 1,
            "projects": [
                {
                    "project_id": "stale",
                    "project_dir": str(tmp_path / "stale"),
                    "project_name": "Stale",
                    "status": "unavailable",
                    "unavailable_since": stale,
                }
            ],
        }
    )

    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SOUL_HOME"] = str(soul_home)
    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "projects", "prune", "--unavailable-days", "30", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["removed_count"] == 1
    assert load_project_registry()["projects"] == []
