from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from soul.services.integrations.queue import queue_status
from soul.services.project_resolver import load_project_registry, projects_registry_path, state_owner_dir_from_record, update_project_registry_records
from soul.services.state_core.review.card import build_review_card
from soul.services.state_core.state_store import utc_now
from soul.services.state_core.working_state import classify_working_item_lifecycle, load_working_state


DAEMON_STATUS_NAME = "daemon_status.json"
REVIEW_INDEX_NAME = "review_index.json"


def daemon_status_path() -> Path:
    return projects_registry_path().with_name(DAEMON_STATUS_NAME)


def review_index_path() -> Path:
    return projects_registry_path().with_name(REVIEW_INDEX_NAME)


def working_lifecycle_summary(state_owner_dir: Path, *, project_name: str, near_expiry_hours: int = 4) -> dict[str, int]:
    doc = load_working_state(state_owner_dir, project_name=project_name)
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
    updated_records: list[dict[str, Any]] = []
    now = utc_now()
    for item in registry.get("projects", []):
        if not isinstance(item, dict):
            continue
        raw_project_dir = item.get("project_dir")
        if not raw_project_dir:
            continue
        project_dir = Path(str(raw_project_dir)).expanduser().resolve()
        state_owner_dir = state_owner_dir_from_record(item)
        base_project = {
            "project_id": item.get("project_id"),
            "project_dir": str(project_dir),
            "project_name": item.get("project_name") or project_dir.name,
            "state_root": item.get("state_root"),
            "storage": item.get("storage"),
        }
        if not project_dir.exists():
            unavailable_since = item.get("unavailable_since") or now
            result = {
                **base_project,
                "available": False,
                "status": "unavailable",
                "error": "project directory does not exist",
                "review": {"ready_to_confirm": 0, "needs_review": 0, "total": 0, "available": 0},
                "queue": {"queued": 0, "started": 0, "failed_retryable": 0, "blocked": 0, "dead_letter": 0, "backlog": 0},
            }
            results.append(result)
            updated_records.append(
                {
                    **item,
                    "status": "unavailable",
                    "last_scanned_at": now,
                    "unavailable_since": unavailable_since,
                    "review": result["review"],
                    "queue": result["queue"],
                }
            )
            continue
        if item.get("storage") != "global" and item.get("state_path") and not Path(str(item["state_path"])).exists():
            unavailable_since = item.get("unavailable_since") or now
            result = {
                **base_project,
                "available": False,
                "status": "unavailable",
                "error": "project state does not exist",
                "review": {"ready_to_confirm": 0, "needs_review": 0, "total": 0, "available": 0},
                "queue": {"queued": 0, "started": 0, "failed_retryable": 0, "blocked": 0, "dead_letter": 0, "backlog": 0},
            }
            results.append(result)
            updated_records.append(
                {
                    **item,
                    "status": "unavailable",
                    "last_scanned_at": now,
                    "unavailable_since": unavailable_since,
                    "review": result["review"],
                    "queue": result["queue"],
                }
            )
            continue
        try:
            card = build_review_card(state_owner_dir, limit=limit, near_expiry_hours=near_expiry_hours)
            queue = queue_status(state_owner_dir)
            counts = card.get("counts", {})
            lifecycle = working_lifecycle_summary(
                state_owner_dir,
                project_name=str(item.get("project_name") or project_dir.name),
                near_expiry_hours=near_expiry_hours,
            )
            review = {
                "ready_to_confirm": counts.get("ready_to_confirm", 0),
                "needs_review": counts.get("needs_review", 0),
                "total": counts.get("total", 0),
                "available": counts.get("available", 0),
            }
            queue_summary = {
                "queued": queue.queued,
                "started": queue.started,
                "failed_retryable": queue.failed_retryable,
                "blocked": queue.blocked,
                "dead_letter": queue.dead_letter,
                "backlog": queue.queued + queue.failed_retryable,
            }
            result = {
                **base_project,
                "project_name": card.get("project") or item.get("project_name") or project_dir.name,
                "available": True,
                "status": "active",
                "lifecycle": lifecycle,
                "review": review,
                "queue": queue_summary,
            }
            results.append(result)
            updated_records.append(
                {
                    **item,
                    "project_name": result["project_name"],
                    "status": "active",
                    "last_scanned_at": now,
                    "last_reviewable_at": now if review["total"] or queue_summary["backlog"] else item.get("last_reviewable_at"),
                    "unavailable_since": None,
                    "review": review,
                    "queue": queue_summary,
                }
            )
        except Exception as exc:
            unavailable_since = item.get("unavailable_since") or now
            result = {
                **base_project,
                "available": False,
                "status": "unavailable",
                "error": str(exc).replace("\n", " ")[:500],
                "review": {"ready_to_confirm": 0, "needs_review": 0, "total": 0, "available": 0},
                "queue": {"queued": 0, "started": 0, "failed_retryable": 0, "blocked": 0, "dead_letter": 0, "backlog": 0},
            }
            results.append(result)
            updated_records.append(
                {
                    **item,
                    "status": "unavailable",
                    "last_scanned_at": now,
                    "unavailable_since": unavailable_since,
                    "review": result["review"],
                    "queue": result["queue"],
                }
            )
    status = {
        "schema_version": 1,
        "generated_at": now,
        "project_count": len(results),
        "projects": results,
    }
    update_project_registry_records(updated_records)
    write_review_index(status)
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


def load_review_index() -> dict[str, Any]:
    path = review_index_path()
    if not path.exists():
        return {"schema_version": 1, "projects": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "projects": []}
    return data if isinstance(data, dict) else {"schema_version": 1, "projects": []}


def write_review_index(index: dict[str, Any]) -> None:
    path = review_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def daemon_loop(*, interval_seconds: float = 300.0, once: bool = False) -> None:
    while True:
        scan_registered_projects()
        if once:
            return
        time.sleep(interval_seconds)
