from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from soul.services.reme.runtime_config import soul_home
from soul.services.shared.constants import SOUL_DIR_NAME, STATE_DIR_NAME
from soul.services.state_core.state_store import utc_now


PROJECTS_REGISTRY_NAME = "projects.json"
GLOBAL_PROJECTS_DIR_NAME = "projects"
STORAGE_GLOBAL = "global"
STORAGE_LOCAL = "local"


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


def find_soul_project_root(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if is_soul_project(candidate):
            return candidate
    return None


def find_git_root(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def is_soul_project(path: Path) -> bool:
    state_dir = path / SOUL_DIR_NAME / STATE_DIR_NAME
    return (state_dir / "state.json").exists() or (state_dir / "STATE.md").exists()


def projects_registry_path() -> Path:
    return soul_home() / PROJECTS_REGISTRY_NAME


def normalize_project_record(record: dict[str, Any]) -> dict[str, Any]:
    raw_project_dir = record.get("project_dir")
    project_dir = Path(str(raw_project_dir or ".")).expanduser().resolve()
    project_id = str(record.get("project_id") or project_id_for_path(project_dir))
    storage = str(record.get("storage") or STORAGE_LOCAL)
    state_root = record.get("state_root")
    if state_root:
        resolved_state_root = Path(str(state_root)).expanduser().resolve()
    elif record.get("state_path"):
        resolved_state_root = Path(str(record["state_path"])).expanduser().resolve().parent
    else:
        resolved_state_root = default_state_root(project_dir, storage=storage, project_id=project_id)
    now = utc_now()
    lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
    next_lifecycle_scan_at = record.get("next_lifecycle_scan_at") or lifecycle.get("next_lifecycle_scan_at")
    return {
        "project_id": project_id,
        "identity": record.get("identity")
        if isinstance(record.get("identity"), dict)
        else {"kind": "path_hash", "source": str(project_dir)},
        "project_dir": str(project_dir),
        "project_name": str(record.get("project_name") or project_dir.name),
        "state_root": str(resolved_state_root),
        "state_path": str(resolved_state_root / "state.json"),
        "reme_root": str(project_dir / SOUL_DIR_NAME / "reme"),
        "traces_root": str(project_dir / SOUL_DIR_NAME / "traces"),
        "storage": storage,
        "status": str(record.get("status") or "active"),
        "first_seen_at": str(record.get("first_seen_at") or record.get("last_seen_at") or now),
        "last_seen_at": str(record.get("last_seen_at") or now),
        "last_scanned_at": record.get("last_scanned_at"),
        "last_reviewable_at": record.get("last_reviewable_at"),
        "next_lifecycle_scan_at": next_lifecycle_scan_at,
        "lifecycle": {**lifecycle, "next_lifecycle_scan_at": next_lifecycle_scan_at},
        "unavailable_since": record.get("unavailable_since"),
        "review": record.get("review") if isinstance(record.get("review"), dict) else {},
        "queue": record.get("queue") if isinstance(record.get("queue"), dict) else {},
    }


def global_project_dir(project_id: str) -> Path:
    return soul_home() / GLOBAL_PROJECTS_DIR_NAME / project_id


def project_id_for_path(project_dir: Path) -> str:
    digest = hashlib.sha1(str(project_dir.expanduser().resolve()).encode("utf-8")).hexdigest()
    return digest[:16]


def default_state_root(project_dir: Path, *, storage: str, project_id: str) -> Path:
    if storage == STORAGE_GLOBAL:
        return global_project_dir(project_id) / SOUL_DIR_NAME / STATE_DIR_NAME
    return project_dir / SOUL_DIR_NAME / STATE_DIR_NAME


def state_owner_dir_from_record(record: dict[str, Any]) -> Path:
    normalized = normalize_project_record(record)
    state_root = Path(str(normalized["state_root"])).expanduser().resolve()
    if state_root.name == STATE_DIR_NAME and state_root.parent.name == SOUL_DIR_NAME:
        return state_root.parent.parent
    return state_root


def register_project(
    project_dir: Path,
    *,
    project_name: str | None = None,
    storage: str = STORAGE_LOCAL,
    state_owner_dir: Path | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    project = project_dir.expanduser().resolve()
    resolved_project_id = project_id or project_id_for_path(project)
    owner_dir = (state_owner_dir or project).expanduser().resolve()
    state_root = default_state_root(project, storage=storage, project_id=resolved_project_id)
    if state_owner_dir is not None:
        state_root = owner_dir / SOUL_DIR_NAME / STATE_DIR_NAME
    registry = load_project_registry()
    projects = [item for item in registry.get("projects", []) if isinstance(item, dict)]
    now = utc_now()
    existing = next(
        (
            item
            for item in projects
            if item.get("project_id") == resolved_project_id or item.get("project_dir") == str(project)
        ),
        None,
    )
    record = {
        "project_id": resolved_project_id,
        "identity": {"kind": "path_hash", "source": str(project)},
        "project_dir": str(project),
        "project_name": project_name or project.name,
        "state_root": str(state_root),
        "state_path": str(state_root / "state.json"),
        "reme_root": str(project / SOUL_DIR_NAME / "reme"),
        "traces_root": str(project / SOUL_DIR_NAME / "traces"),
        "storage": storage,
        "status": "active",
        "first_seen_at": (existing or {}).get("first_seen_at") or now,
        "last_seen_at": now,
        "last_scanned_at": (existing or {}).get("last_scanned_at"),
        "last_reviewable_at": (existing or {}).get("last_reviewable_at"),
        "next_lifecycle_scan_at": (existing or {}).get("next_lifecycle_scan_at"),
        "lifecycle": {
            **((existing or {}).get("lifecycle") if isinstance((existing or {}).get("lifecycle"), dict) else {}),
            "next_lifecycle_scan_at": (existing or {}).get("next_lifecycle_scan_at"),
        },
        "unavailable_since": None,
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


def register_auto_project(raw_project_dir: str | Path | None = None, *, cwd: str | Path | None = None) -> dict[str, Any] | None:
    start = Path(raw_project_dir or cwd or Path.cwd()).expanduser().resolve()
    git_root = find_git_root(start)
    if git_root is None:
        return None
    project_id = project_id_for_path(git_root)
    return register_project(
        git_root,
        project_name=git_root.name,
        storage=STORAGE_LOCAL,
        state_owner_dir=git_root,
        project_id=project_id,
    )


def load_project_registry() -> dict[str, Any]:
    path = projects_registry_path()
    if not path.exists():
        return {"schema_version": 1, "projects": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "projects": []}
    if not isinstance(data, dict):
        return {"schema_version": 1, "projects": []}
    projects = [normalize_project_record(item) for item in data.get("projects", []) if isinstance(item, dict)]
    return {
        "schema_version": data.get("schema_version", 1),
        "updated_at": data.get("updated_at"),
        "projects": projects,
    }


def update_project_registry_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    now = utc_now()
    registry = {
        "schema_version": 1,
        "updated_at": now,
        "projects": sorted(
            [normalize_project_record(record) for record in records],
            key=lambda item: str(item.get("last_seen_at", "")),
            reverse=True,
        ),
    }
    save_project_registry(registry)
    return registry


def prune_unavailable_projects(*, unavailable_days: int) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(unavailable_days, 0))
    registry = load_project_registry()
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for record in registry.get("projects", []):
        if not isinstance(record, dict):
            continue
        if record.get("status") != "unavailable":
            kept.append(record)
            continue
        unavailable_since = parse_registry_time(record.get("unavailable_since"))
        if unavailable_since is None or unavailable_since > cutoff:
            kept.append(record)
            continue
        removed.append(record)
    next_registry = update_project_registry_records(kept) if removed or projects_registry_path().exists() else registry
    return {
        "schema_version": 1,
        "removed_count": len(removed),
        "kept_count": len(kept),
        "unavailable_days": unavailable_days,
        "removed": removed,
        "registry": next_registry,
    }


def parse_registry_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def save_project_registry(registry: dict[str, Any]) -> None:
    path = projects_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
