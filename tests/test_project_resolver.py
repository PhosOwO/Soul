from __future__ import annotations

import json
from pathlib import Path

from soul.services.project_resolver import (
    find_project_root,
    global_project_dir,
    load_project_registry,
    register_project,
    register_auto_project,
    resolve_project_dir,
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
