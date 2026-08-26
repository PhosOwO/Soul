from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from soul.services.integrations.queue import (
    EVENT_BLOCKED,
    EVENT_COMPLETED,
    EVENT_DEAD_LETTER,
    EVENT_FAILED,
    EVENT_QUEUED,
    EVENT_STARTED,
    parse_time,
    queue_status,
    replay_queue_state,
)
from soul.services.project_resolver import load_project_registry, projects_registry_path, state_owner_dir_from_record, update_project_registry_records
from soul.services.state_core.review.card import build_review_card
from soul.services.state_core.state_store import utc_now
from soul.services.state_core.working_state import classify_working_item_lifecycle, load_working_state


DAEMON_STATUS_NAME = "daemon_status.json"
REVIEW_INDEX_NAME = "review_index.json"
NOTIFICATION_STATE_NAME = "notification_state.json"
PROJECT_NOTIFICATION_COOLDOWN_MINUTES = 120
GLOBAL_NOTIFICATION_COOLDOWN_MINUTES = 15
QUEUE_BACKLOG_NOTIFICATION_AFTER_MINUTES = 30


def daemon_status_path() -> Path:
    return projects_registry_path().with_name(DAEMON_STATUS_NAME)


def review_index_path() -> Path:
    return projects_registry_path().with_name(REVIEW_INDEX_NAME)


def notification_state_path() -> Path:
    return projects_registry_path().with_name(NOTIFICATION_STATE_NAME)


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


def empty_review_summary() -> dict[str, int]:
    return {"ready_to_confirm": 0, "needs_review": 0, "total": 0, "available": 0}


def empty_queue_summary() -> dict[str, int]:
    return {"queued": 0, "started": 0, "failed_retryable": 0, "blocked": 0, "dead_letter": 0, "backlog": 0}


def oldest_queue_backlog_at(state_owner_dir: Path) -> str | None:
    state = replay_queue_state(state_owner_dir)
    completed_keys = {
        str(event.get("idempotency_key"))
        for event in state["events"]
        if event.get("event") == EVENT_COMPLETED and event.get("idempotency_key")
    }
    oldest: datetime | None = None
    for job in state["jobs"]:
        job_id = str(job.get("job_id") or "")
        idempotency_key = str(job.get("idempotency_key") or job_id)
        if idempotency_key in completed_keys:
            continue
        status = state["status_by_job"].get(job_id, {})
        event = status.get("event", EVENT_QUEUED)
        if event in {EVENT_BLOCKED, EVENT_DEAD_LETTER}:
            continue
        if event not in {EVENT_QUEUED, EVENT_STARTED, EVENT_FAILED}:
            continue
        if event == EVENT_FAILED and not status.get("retryable", False):
            continue
        created_at = parse_time(str(job.get("created_at") or ""))
        if oldest is None or created_at < oldest:
            oldest = created_at
    if oldest is None:
        return None
    return oldest.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def scan_project_record(
    record: dict[str, Any],
    *,
    now: str,
    limit: int = 5,
    near_expiry_hours: int = 4,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_project_dir = record.get("project_dir")
    project_dir = Path(str(raw_project_dir or ".")).expanduser().resolve()
    state_owner_dir = state_owner_dir_from_record(record)
    base_project = {
        "project_id": record.get("project_id"),
        "project_dir": str(project_dir),
        "project_name": record.get("project_name") or project_dir.name,
        "state_root": record.get("state_root"),
        "storage": record.get("storage"),
    }
    if not raw_project_dir or not project_dir.exists():
        result = {
            **base_project,
            "available": False,
            "status": "unavailable",
            "error": "project directory does not exist",
            "review": empty_review_summary(),
            "queue": empty_queue_summary(),
        }
        return result, {
            **record,
            "status": "unavailable",
            "last_scanned_at": now,
            "unavailable_since": record.get("unavailable_since") or now,
            "review": result["review"],
            "queue": result["queue"],
        }
    if record.get("storage") != "global" and record.get("state_path") and not Path(str(record["state_path"])).exists():
        result = {
            **base_project,
            "available": False,
            "status": "unavailable",
            "error": "project state does not exist",
            "review": empty_review_summary(),
            "queue": empty_queue_summary(),
        }
        return result, {
            **record,
            "status": "unavailable",
            "last_scanned_at": now,
            "unavailable_since": record.get("unavailable_since") or now,
            "review": result["review"],
            "queue": result["queue"],
        }
    try:
        project_name = str(record.get("project_name") or project_dir.name)
        card = build_review_card(
            state_owner_dir,
            limit=limit,
            near_expiry_hours=near_expiry_hours,
            project_name=project_name,
            source_project_dir=project_dir,
        )
        queue = queue_status(state_owner_dir)
        counts = card.get("counts", {})
        lifecycle = working_lifecycle_summary(
            state_owner_dir,
            project_name=project_name,
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
            "oldest_backlog_at": oldest_queue_backlog_at(state_owner_dir),
        }
        result = {
            **base_project,
            "project_name": card.get("project") or project_name,
            "available": True,
            "status": "active",
            "lifecycle": lifecycle,
            "review": review,
            "queue": queue_summary,
        }
        return result, {
            **record,
            "project_name": result["project_name"],
            "status": "active",
            "last_scanned_at": now,
            "last_reviewable_at": now if review["total"] or queue_summary["backlog"] else record.get("last_reviewable_at"),
            "unavailable_since": None,
            "review": review,
            "queue": queue_summary,
        }
    except Exception as exc:
        result = {
            **base_project,
            "available": False,
            "status": "unavailable",
            "error": str(exc).replace("\n", " ")[:500],
            "review": empty_review_summary(),
            "queue": empty_queue_summary(),
        }
        return result, {
            **record,
            "status": "unavailable",
            "last_scanned_at": now,
            "unavailable_since": record.get("unavailable_since") or now,
            "review": result["review"],
            "queue": result["queue"],
        }


def scan_registered_projects(*, limit: int = 5, near_expiry_hours: int = 4) -> dict[str, Any]:
    registry = load_project_registry()
    results: list[dict[str, Any]] = []
    updated_records: list[dict[str, Any]] = []
    now = utc_now()
    for item in registry.get("projects", []):
        if not isinstance(item, dict):
            continue
        result, updated_record = scan_project_record(item, now=now, limit=limit, near_expiry_hours=near_expiry_hours)
        results.append(result)
        updated_records.append(updated_record)
    status = {
        "schema_version": 1,
        "generated_at": now,
        "project_count": len(results),
        "projects": results,
    }
    if updated_records or projects_registry_path().exists():
        update_project_registry_records(updated_records)
    write_review_index(status)
    write_daemon_status(status)
    return status


def refresh_registered_project(
    project_id: str,
    *,
    limit: int = 5,
    near_expiry_hours: int = 4,
) -> dict[str, Any] | None:
    registry = load_project_registry()
    records = [item for item in registry.get("projects", []) if isinstance(item, dict)]
    target = next((item for item in records if item.get("project_id") == project_id), None)
    if target is None:
        return None
    now = utc_now()
    result, updated_record = scan_project_record(target, now=now, limit=limit, near_expiry_hours=near_expiry_hours)
    updated_records = [updated_record if item.get("project_id") == project_id else item for item in records]
    update_project_registry_records(updated_records)
    merge_review_project(result, generated_at=now)
    return result


def refresh_registered_project_for_state_owner(state_owner_dir: Path) -> dict[str, Any] | None:
    owner = state_owner_dir.expanduser().resolve()
    registry = load_project_registry()
    for record in registry.get("projects", []):
        if isinstance(record, dict) and state_owner_dir_from_record(record) == owner:
            return refresh_registered_project(str(record.get("project_id") or ""))
    return None


def merge_review_project(project: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    index = load_review_index()
    projects = [item for item in index.get("projects", []) if isinstance(item, dict)]
    project_id = project.get("project_id")
    replaced = False
    merged: list[dict[str, Any]] = []
    for item in projects:
        if item.get("project_id") == project_id:
            merged.append(project)
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(project)
    next_index = {
        **index,
        "schema_version": 1,
        "generated_at": generated_at,
        "project_count": len(merged),
        "projects": merged,
    }
    write_review_index(next_index)
    write_daemon_status(next_index)
    return next_index


def load_notification_state() -> dict[str, Any]:
    path = notification_state_path()
    if not path.exists():
        return {"schema_version": 1, "projects": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "projects": {}}
    if not isinstance(data, dict):
        return {"schema_version": 1, "projects": {}}
    if not isinstance(data.get("projects"), dict):
        data["projects"] = {}
    return data


def write_notification_state(state: dict[str, Any]) -> None:
    path = notification_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def notify_review_index(
    index: dict[str, Any] | None = None,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    current_index = index or load_review_index()
    current_time = now or datetime.now(UTC)
    current_iso = current_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state = load_notification_state()
    projects_state = state.get("projects") if isinstance(state.get("projects"), dict) else {}
    candidates = notification_candidates(current_index, state, now=current_time)
    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": current_iso,
        "dry_run": dry_run,
        "would_notify": bool(candidates),
        "notifications": candidates,
        "delivery": {"attempted": False, "delivered": False, "platform": platform_name or sys.platform},
    }
    if not dry_run:
        for project in current_index.get("projects", []):
            if not isinstance(project, dict):
                continue
            project_id = str(project.get("project_id") or "")
            if not project_id:
                continue
            project_state = projects_state.get(project_id) if isinstance(projects_state.get(project_id), dict) else {}
            projects_state[project_id] = {
                **project_state,
                "last_observed_at": current_iso,
                "last_observed_counts": notification_counts(project),
            }
        if candidates:
            title, body = format_notification_message(candidates)
            delivery = send_desktop_notification(title, body, platform_name=platform_name)
            result["delivery"] = delivery
            if delivery.get("delivered") or delivery.get("skipped"):
                state["last_notified_at"] = current_iso
                for candidate in candidates:
                    project_id = str(candidate.get("project_id") or "")
                    if not project_id:
                        continue
                    project_state = projects_state.get(project_id) if isinstance(projects_state.get(project_id), dict) else {}
                    projects_state[project_id] = {
                        **project_state,
                        "last_signature": candidate.get("signature"),
                        "last_notified_at": current_iso,
                        "last_notified_counts": candidate.get("counts"),
                    }
        state["schema_version"] = 1
        state["updated_at"] = current_iso
        state["projects"] = projects_state
        state["last_result"] = result
        write_notification_state(state)
    return result


def notification_candidates(
    index: dict[str, Any],
    state: dict[str, Any],
    *,
    now: datetime,
    project_cooldown_minutes: int = PROJECT_NOTIFICATION_COOLDOWN_MINUTES,
    global_cooldown_minutes: int = GLOBAL_NOTIFICATION_COOLDOWN_MINUTES,
) -> list[dict[str, Any]]:
    last_global = parse_utc_time(state.get("last_notified_at"))
    if last_global and now - last_global < timedelta(minutes=global_cooldown_minutes):
        return []
    projects_state = state.get("projects") if isinstance(state.get("projects"), dict) else {}
    candidates: list[dict[str, Any]] = []
    for project in index.get("projects", []):
        if not isinstance(project, dict) or project.get("available") is False:
            continue
        project_id = str(project.get("project_id") or "")
        if not project_id:
            continue
        counts = notification_counts(project)
        queue_old_enough = queue_backlog_old_enough(project, now=now)
        if counts["review_total"] <= 0 and not queue_old_enough:
            continue
        previous = projects_state.get(project_id) if isinstance(projects_state.get(project_id), dict) else {}
        last_project = parse_utc_time(previous.get("last_notified_at"))
        signature = notification_signature(counts)
        if previous.get("last_signature") == signature and last_project:
            if now - last_project < timedelta(minutes=project_cooldown_minutes):
                continue
        notified_counts = previous.get("last_notified_counts") if isinstance(previous.get("last_notified_counts"), dict) else {}
        reason = notification_reason(counts, notified_counts, queue_old_enough=queue_old_enough)
        if reason is None:
            continue
        candidates.append(
            {
                "project_id": project_id,
                "project_name": project.get("project_name") or "unknown",
                "project_dir": project.get("project_dir") or "",
                "reason": reason,
                "signature": signature,
                "counts": counts,
            }
        )
    return candidates


def notification_counts(project: dict[str, Any]) -> dict[str, int]:
    review = project.get("review") if isinstance(project.get("review"), dict) else {}
    queue = project.get("queue") if isinstance(project.get("queue"), dict) else {}
    return {
        "needs_review": int(review.get("needs_review") or 0),
        "ready_to_confirm": int(review.get("ready_to_confirm") or 0),
        "review_total": int(review.get("total") or 0),
        "queue_backlog": int(queue.get("backlog") or 0),
    }


def notification_signature(counts: dict[str, int]) -> str:
    return (
        f"needs={counts.get('needs_review', 0)};"
        f"ready={counts.get('ready_to_confirm', 0)};"
        f"backlog={counts.get('queue_backlog', 0)}"
    )


def notification_reason(
    counts: dict[str, int],
    previous_counts: dict[str, Any],
    *,
    queue_old_enough: bool = False,
) -> str | None:
    previous_needs = int(previous_counts.get("needs_review") or 0)
    previous_ready = int(previous_counts.get("ready_to_confirm") or 0)
    previous_total = int(previous_counts.get("review_total") or 0)
    if counts["needs_review"] > previous_needs:
        return "needs_review_increased"
    if counts["ready_to_confirm"] > previous_ready:
        return "ready_to_confirm_increased"
    if previous_total == 0 and counts["review_total"] > 0:
        return "review_items_available"
    if counts["review_total"] > 0:
        return "review_items_still_pending"
    if queue_old_enough and counts["queue_backlog"] > 0:
        return "queue_backlog_stale"
    return None


def queue_backlog_old_enough(
    project: dict[str, Any],
    *,
    now: datetime,
    threshold_minutes: int = QUEUE_BACKLOG_NOTIFICATION_AFTER_MINUTES,
) -> bool:
    queue = project.get("queue") if isinstance(project.get("queue"), dict) else {}
    if int(queue.get("backlog") or 0) <= 0:
        return False
    oldest = parse_utc_time(queue.get("oldest_backlog_at"))
    if oldest is None:
        return False
    return now - oldest >= timedelta(minutes=threshold_minutes)


def format_notification_message(candidates: list[dict[str, Any]]) -> tuple[str, str]:
    review_total = sum(int(item.get("counts", {}).get("review_total") or 0) for item in candidates)
    queue_total = sum(int(item.get("counts", {}).get("queue_backlog") or 0) for item in candidates)
    if len(candidates) == 1:
        item = candidates[0]
        project_name = item.get("project_name", "unknown")
        if review_total:
            return "Soul Review", f"{project_name} has {review_total} items ready for review"
        return "Soul Review", f"{project_name} has {queue_total} queued jobs waiting"
    if review_total:
        return "Soul Review", f"{len(candidates)} projects have {review_total} items ready for review"
    return "Soul Review", f"{len(candidates)} projects have {queue_total} queued jobs waiting"


def send_desktop_notification(title: str, body: str, *, platform_name: str | None = None) -> dict[str, Any]:
    platform = platform_name or sys.platform
    if platform != "darwin":
        return {"attempted": False, "delivered": False, "skipped": True, "platform": platform}
    script = f"display notification {applescript_string(body)} with title {applescript_string(title)}"
    try:
        completed = subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)
    except OSError as exc:
        return {"attempted": True, "delivered": False, "platform": platform, "error": str(exc)}
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "").strip()
        return {"attempted": True, "delivered": False, "platform": platform, "error": error}
    return {"attempted": True, "delivered": True, "platform": platform}


def applescript_string(value: str) -> str:
    return json.dumps(value)


def parse_utc_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
        status = scan_registered_projects()
        notify_review_index(status)
        if once:
            return
        time.sleep(interval_seconds)
