from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from soul.services.reme.runtime_config import soul_home
from soul.services.shared.constants import SOUL_DIR_NAME, STATE_DIR_NAME
from soul.services.state_core.state_store import utc_now


PROJECTS_REGISTRY_NAME = "projects.json"


def resolve_project_dir(
    raw_project_dir: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    create: bool = False,
) -> Path:
    start = Path(raw_project_dir or cwd or Path.cwd()).expanduser().resolve()
    if raw_project_dir:
        return start

    found = find_project_root(start)
    if found is not None:
        return found
    if create:
        return start
    return start


def find_project_root(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if is_soul_project(candidate):
            return candidate
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def is_soul_project(path: Path) -> bool:
    state_dir = path / SOUL_DIR_NAME / STATE_DIR_NAME
    return (state_dir / "state.json").exists() or (state_dir / "STATE.md").exists()


def projects_registry_path() -> Path:
    return soul_home() / PROJECTS_REGISTRY_NAME


def register_project(project_dir: Path, *, project_name: str | None = None) -> dict[str, Any]:
    project = project_dir.expanduser().resolve()
    registry = load_project_registry()
    projects = [item for item in registry.get("projects", []) if isinstance(item, dict)]
    now = utc_now()
    existing = next((item for item in projects if item.get("project_dir") == str(project)), None)
    record = {
        "project_dir": str(project),
        "project_name": project_name or project.name,
        "state_path": str(project / SOUL_DIR_NAME / STATE_DIR_NAME / "state.json"),
        "last_seen_at": now,
    }
    if existing is None:
        projects.append(record)
    else:
        existing.update(record)
        record = existing
    registry = {
        "schema_version": 1,
        "updated_at": now,
        "projects": sorted(projects, key=lambda item: str(item.get("last_seen_at", "")), reverse=True),
    }
    try:
        save_project_registry(registry)
    except OSError:
        pass
    return record


def load_project_registry() -> dict[str, Any]:
    path = projects_registry_path()
    if not path.exists():
        return {"schema_version": 1, "projects": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "projects": []}
    return data if isinstance(data, dict) else {"schema_version": 1, "projects": []}


def save_project_registry(registry: dict[str, Any]) -> None:
    path = projects_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
