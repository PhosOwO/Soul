from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from soul.services.integrations.queue import queue_status
from soul.services.project_resolver import load_project_registry, projects_registry_path
from soul.services.state_core.review.card import build_review_card
from soul.services.state_core.state_store import utc_now
from soul.services.state_core.working_state import classify_working_item_lifecycle, load_working_state


DAEMON_STATUS_NAME = "daemon_status.json"


def daemon_status_path() -> Path:
    return projects_registry_path().with_name(DAEMON_STATUS_NAME)


def working_lifecycle_summary(project_dir: Path, *, near_expiry_hours: int = 4) -> dict[str, int]:
    doc = load_working_state(project_dir, project_name=project_dir.name)
    summary = {
        "total": 0,
        "active_working": 0,
        "review_due": 0,
        "near_expiry": 0,
        "expired_unresolved": 0,
        "ready_to_confirm": 0,
        "needs_review": 0,
    }
    for item in doc.get("items", []):
        lifecycle = classify_working_item_lifecycle(item, near_expiry_hours=near_expiry_hours)
        summary["total"] += 1
        if lifecycle.active_for_context:
            summary["active_working"] += 1
        if lifecycle.review_due:
            summary["review_due"] += 1
        if lifecycle.near_expiry:
            summary["near_expiry"] += 1
        if lifecycle.expired and lifecycle.status in {"working", "conflict_needs_review"}:
            summary["expired_unresolved"] += 1
        if lifecycle.review_bucket == "ready_to_confirm":
            summary["ready_to_confirm"] += 1
        elif lifecycle.review_bucket == "needs_review":
            summary["needs_review"] += 1
    return summary


def scan_registered_projects(*, limit: int = 5, near_expiry_hours: int = 4) -> dict[str, Any]:
    registry = load_project_registry()
    results: list[dict[str, Any]] = []
    for item in registry.get("projects", []):
        if not isinstance(item, dict):
            continue
        raw_project_dir = item.get("project_dir")
        if not raw_project_dir:
            continue
        project_dir = Path(str(raw_project_dir)).expanduser().resolve()
        if not project_dir.exists():
            results.append(
                {
                    "project_dir": str(project_dir),
                    "project_name": item.get("project_name") or project_dir.name,
                    "available": False,
                    "error": "project directory does not exist",
                }
            )
            continue
        try:
            card = build_review_card(project_dir, limit=limit, near_expiry_hours=near_expiry_hours)
            queue = queue_status(project_dir)
            counts = card.get("counts", {})
            lifecycle = working_lifecycle_summary(project_dir, near_expiry_hours=near_expiry_hours)
            results.append(
                {
                    "project_dir": str(project_dir),
                    "project_name": card.get("project") or item.get("project_name") or project_dir.name,
                    "available": True,
                    "lifecycle": lifecycle,
                    "review": {
                        "ready_to_confirm": counts.get("ready_to_confirm", 0),
                        "needs_review": counts.get("needs_review", 0),
                        "total": counts.get("total", 0),
                        "available": counts.get("available", 0),
                    },
                    "queue": {
                        "queued": queue.queued,
                        "started": queue.started,
                        "failed_retryable": queue.failed_retryable,
                        "blocked": queue.blocked,
                        "dead_letter": queue.dead_letter,
                        "backlog": queue.queued + queue.failed_retryable,
                    },
                }
            )
        except Exception as exc:
            results.append(
                {
                    "project_dir": str(project_dir),
                    "project_name": item.get("project_name") or project_dir.name,
                    "available": False,
                    "error": str(exc).replace("\n", " ")[:500],
                }
            )
    status = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "project_count": len(results),
        "projects": results,
    }
    write_daemon_status(status)
    return status


def load_daemon_status() -> dict[str, Any]:
    path = daemon_status_path()
    if not path.exists():
        return {"schema_version": 1, "projects": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "projects": []}
    return data if isinstance(data, dict) else {"schema_version": 1, "projects": []}


def write_daemon_status(status: dict[str, Any]) -> None:
    path = daemon_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def daemon_loop(*, interval_seconds: float = 300.0, once: bool = False) -> None:
    while True:
        scan_registered_projects()
        if once:
            return
        time.sleep(interval_seconds)
