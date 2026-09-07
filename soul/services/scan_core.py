from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

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
from soul.services.notifications.manager import notifier_for_platform
from soul.services.notifications.macos import applescript_string
from soul.services.project_resolver import load_project_registry, projects_registry_path, state_owner_dir_from_record, update_project_registry_records
from soul.services.state_core.review.card import build_review_card
from soul.services.state_core.state_store import utc_now
from soul.services.state_core.working_state import classify_working_item_lifecycle, load_working_state
from soul.services.shared.state_types import WorkingStateItem


REVIEW_INDEX_NAME = "review_index.json"
NOTIFICATION_STATE_NAME = "notification_state.json"
PROJECT_NOTIFICATION_COOLDOWN_MINUTES = 120
GLOBAL_NOTIFICATION_COOLDOWN_MINUTES = 15
QUEUE_BACKLOG_NOTIFICATION_AFTER_MINUTES = 30
UNAVAILABLE_DEFAULT_INBOX_DAYS = 30


def mapping_or_empty(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def review_index_path() -> Path:
    return projects_registry_path().with_name(REVIEW_INDEX_NAME)


def notification_state_path() -> Path:
    return projects_registry_path().with_name(NOTIFICATION_STATE_NAME)


def working_lifecycle_summary(
    state_owner_dir: Path,
    *,
    project_name: str,
    near_expiry_hours: int = 4,
    now: datetime | None = None,
) -> dict[str, int | str | None]:
    doc = load_working_state(state_owner_dir, project_name=project_name)
    current_time = now or datetime.now(UTC)
    summary = {
        "total": 0,
        "active_working": 0,
        "review_due": 0,
        "near_expiry": 0,
        "expired_unresolved": 0,
        "ready_to_confirm": 0,
        "needs_review": 0,
        "next_lifecycle_scan_at": None,
    }
    for item in doc.get("items", []):
        if not isinstance(item, dict):
            continue
        working_item = cast(WorkingStateItem, item)
        lifecycle = classify_working_item_lifecycle(working_item, near_expiry_hours=near_expiry_hours, now=current_time)
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
        next_boundary = next_working_lifecycle_boundary(working_item, now=current_time, near_expiry_hours=near_expiry_hours)
        if next_boundary and (
            summary["next_lifecycle_scan_at"] is None
            or next_boundary < str(summary["next_lifecycle_scan_at"])
        ):
            summary["next_lifecycle_scan_at"] = next_boundary
    return summary


def next_working_lifecycle_boundary(item: WorkingStateItem, *, now: datetime, near_expiry_hours: int = 4) -> str | None:
    candidates: list[datetime] = []
    for key in ["review_after", "expires_at"]:
        parsed = parse_utc_time(item.get(key))
        if parsed and parsed > now:
            candidates.append(parsed)
    expires_at = parse_utc_time(item.get("expires_at"))
    if expires_at is not None:
        near_expiry_at = expires_at - timedelta(hours=near_expiry_hours)
        if near_expiry_at > now:
            candidates.append(near_expiry_at)
    if not candidates:
        return None
    return min(candidates).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def empty_review_summary() -> dict[str, int]:
    return {"ready_to_confirm": 0, "needs_review": 0, "total": 0, "available": 0}


def empty_queue_summary() -> dict[str, int]:
    return {"queued": 0, "started": 0, "failed_retryable": 0, "blocked": 0, "dead_letter": 0, "backlog": 0}


def review_index_in_default_inbox(project: dict[str, Any], *, now: datetime | None = None, unavailable_days: int = UNAVAILABLE_DEFAULT_INBOX_DAYS) -> bool:
    status = str(project.get("status") or "active")
    if status == "archived":
        return False
    review = mapping_or_empty(project.get("review"))
    queue = mapping_or_empty(project.get("queue"))
    if status != "unavailable" and project.get("available") is not False:
        return int(review.get("total") or 0) > 0 or int(queue.get("backlog") or 0) > 0
    if not project.get("last_reviewable_at"):
        return False
    unavailable_since = parse_utc_time(project.get("unavailable_since"))
    if unavailable_since is None:
        return True
    current_time = now or datetime.now(UTC)
    return current_time - unavailable_since < timedelta(days=max(unavailable_days, 0))


def with_default_inbox_flag(project: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    return {**project, "in_default_inbox": review_index_in_default_inbox(project, now=now)}


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
        "identity": record.get("identity"),
        "project_dir": str(project_dir),
        "project_name": record.get("project_name") or project_dir.name,
        "state_root": record.get("state_root"),
        "reme_root": record.get("reme_root"),
        "traces_root": record.get("traces_root"),
        "last_reviewable_at": record.get("last_reviewable_at"),
        "unavailable_since": record.get("unavailable_since"),
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
        result = with_default_inbox_flag(result, now=parse_utc_time(now))
        return result, {
            **record,
            "status": "unavailable",
            "last_scanned_at": now,
            "unavailable_since": record.get("unavailable_since") or now,
            "review": result["review"],
            "queue": result["queue"],
            "lifecycle": mapping_or_empty(record.get("lifecycle")),
        }
    if record.get("state_path") and not Path(str(record["state_path"])).exists():
        result = {
            **base_project,
            "available": False,
            "status": "unavailable",
            "error": "project state does not exist",
            "review": empty_review_summary(),
            "queue": empty_queue_summary(),
        }
        result = with_default_inbox_flag(result, now=parse_utc_time(now))
        return result, {
            **record,
            "status": "unavailable",
            "last_scanned_at": now,
            "unavailable_since": record.get("unavailable_since") or now,
            "review": result["review"],
            "queue": result["queue"],
            "lifecycle": mapping_or_empty(record.get("lifecycle")),
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
        scan_time = parse_utc_time(now) or datetime.now(UTC)
        lifecycle = working_lifecycle_summary(
            state_owner_dir,
            project_name=project_name,
            near_expiry_hours=near_expiry_hours,
            now=scan_time,
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
        result = with_default_inbox_flag(result, now=scan_time)
        return result, {
            **record,
            "project_name": result["project_name"],
            "status": "active",
            "last_scanned_at": now,
            "last_reviewable_at": now if review["total"] or queue_summary["backlog"] else record.get("last_reviewable_at"),
            "next_lifecycle_scan_at": lifecycle.get("next_lifecycle_scan_at"),
            "lifecycle": lifecycle,
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
        result = with_default_inbox_flag(result, now=parse_utc_time(now))
        return result, {
            **record,
            "status": "unavailable",
            "last_scanned_at": now,
            "unavailable_since": record.get("unavailable_since") or now,
            "review": result["review"],
            "queue": result["queue"],
            "lifecycle": mapping_or_empty(record.get("lifecycle")),
        }


def scan_registered_projects(
    *,
    limit: int = 5,
    near_expiry_hours: int = 4,
    due_only: bool = False,
) -> dict[str, Any]:
    registry = load_project_registry()
    results: list[dict[str, Any]] = []
    updated_records: list[dict[str, Any]] = []
    now = utc_now()
    current_time = parse_utc_time(now) or datetime.now(UTC)
    for item in registry.get("projects", []):
        if not isinstance(item, dict):
            continue
        if due_only and not should_scan_project_record(item, now=current_time):
            normalized = with_default_inbox_flag(project_result_from_registry_record(item), now=current_time)
            normalized["scan_skipped"] = True
            results.append(normalized)
            updated_records.append(item)
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
    return status


def should_scan_project_record(record: dict[str, Any], *, now: datetime) -> bool:
    if record.get("status") == "unavailable":
        return True
    review = mapping_or_empty(record.get("review"))
    queue = mapping_or_empty(record.get("queue"))
    if int(review.get("total") or 0) > 0:
        return True
    if int(queue.get("backlog") or 0) > 0:
        return True
    next_scan = parse_utc_time(record.get("next_lifecycle_scan_at"))
    return next_scan is None or next_scan <= now


def project_result_from_registry_record(record: dict[str, Any]) -> dict[str, Any]:
    project_dir = Path(str(record.get("project_dir") or ".")).expanduser().resolve()
    return {
        "project_id": record.get("project_id"),
        "identity": record.get("identity"),
        "project_dir": str(project_dir),
        "project_name": record.get("project_name") or project_dir.name,
        "state_root": record.get("state_root"),
        "reme_root": record.get("reme_root"),
        "traces_root": record.get("traces_root"),
        "last_reviewable_at": record.get("last_reviewable_at"),
        "unavailable_since": record.get("unavailable_since"),
        "storage": record.get("storage"),
        "available": record.get("status") != "unavailable",
        "status": record.get("status") or "active",
        "lifecycle": {"next_lifecycle_scan_at": record.get("next_lifecycle_scan_at")},
        "review": mapping_or_empty(record.get("review")) or empty_review_summary(),
        "queue": mapping_or_empty(record.get("queue")) or empty_queue_summary(),
    }


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
    review_host: str = "127.0.0.1",
    review_port: int = 8765,
) -> dict[str, Any]:
    current_index = index or load_review_index()
    current_time = now or datetime.now(UTC)
    current_iso = current_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state = load_notification_state()
    projects_state = mapping_or_empty(state.get("projects"))
    candidates = notification_candidates(current_index, state, now=current_time)
    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": current_iso,
        "dry_run": dry_run,
        "would_notify": bool(candidates),
        "reason": "notification_candidates" if candidates else notification_skip_reason(current_index, state, now=current_time),
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
            project_state = mapping_or_empty(projects_state.get(project_id))
            projects_state[project_id] = {
                **project_state,
                "last_observed_at": current_iso,
                "last_observed_counts": notification_counts(project),
            }
        if candidates:
            title, body = format_notification_message(candidates)
            action = review_notification_action(candidates, host=review_host, port=review_port)
            try:
                delivery = send_desktop_notification(title, body, platform_name=platform_name, action=action)
            except Exception as exc:
                delivery = {
                    "attempted": True,
                    "delivered": False,
                    "platform": platform_name or sys.platform,
                    "error": str(exc).replace("\n", " ")[:500],
                }
            result["delivery"] = delivery
            if delivery.get("delivered") or delivery.get("skipped"):
                state["last_notified_at"] = current_iso
                for candidate in candidates:
                    project_id = str(candidate.get("project_id") or "")
                    if not project_id:
                        continue
                    project_state = mapping_or_empty(projects_state.get(project_id))
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


def notification_skip_reason(index: dict[str, Any], state: dict[str, Any], *, now: datetime) -> str:
    last_global = parse_utc_time(state.get("last_notified_at"))
    if last_global and now - last_global < timedelta(minutes=GLOBAL_NOTIFICATION_COOLDOWN_MINUTES):
        return "global_cooldown"
    projects_state = mapping_or_empty(state.get("projects"))
    has_attention = False
    for project in index.get("projects", []):
        if not isinstance(project, dict) or project.get("available") is False:
            continue
        counts = notification_counts(project)
        if counts["review_total"] <= 0 and not queue_backlog_old_enough(project, now=now):
            continue
        has_attention = True
        project_id = str(project.get("project_id") or "")
        previous = mapping_or_empty(projects_state.get(project_id))
        last_project = parse_utc_time(previous.get("last_notified_at"))
        if previous.get("last_signature") == notification_signature(counts) and last_project:
            return "project_signature_unchanged"
    return "no_new_notification_changes" if has_attention else "no_reviewable_notification_candidates"


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
    projects_state = mapping_or_empty(state.get("projects"))
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
        previous = mapping_or_empty(projects_state.get(project_id))
        last_project = parse_utc_time(previous.get("last_notified_at"))
        signature = notification_signature(counts)
        if previous.get("last_signature") == signature and last_project:
            if now - last_project < timedelta(minutes=project_cooldown_minutes):
                continue
        notified_counts = mapping_or_empty(previous.get("last_notified_counts"))
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
    review = mapping_or_empty(project.get("review"))
    queue = mapping_or_empty(project.get("queue"))
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
    previous_queue = int(previous_counts.get("queue_backlog") or 0)
    if counts["needs_review"] > previous_needs:
        return "needs_review_increased"
    if counts["ready_to_confirm"] > previous_ready:
        return "ready_to_confirm_increased"
    if previous_total == 0 and counts["review_total"] > 0:
        return "review_items_available"
    if queue_old_enough and counts["queue_backlog"] > previous_queue:
        return "queue_backlog_stale"
    return None


def queue_backlog_old_enough(
    project: dict[str, Any],
    *,
    now: datetime,
    threshold_minutes: int = QUEUE_BACKLOG_NOTIFICATION_AFTER_MINUTES,
) -> bool:
    queue = mapping_or_empty(project.get("queue"))
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


def review_notification_action(candidates: list[dict[str, Any]], *, host: str, port: int) -> dict[str, Any]:
    project_dir = "."
    if len(candidates) == 1 and candidates[0].get("project_dir"):
        project_dir = str(candidates[0]["project_dir"])
    return {
        "kind": "review",
        "project_dir": project_dir,
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}/review",
    }


def send_desktop_notification(
    title: str,
    body: str,
    *,
    platform_name: str | None = None,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return notifier_for_platform(platform_name).send(title, body, action=action)


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


def scan_loop(*, interval_seconds: float = 300.0, once: bool = False) -> None:
    while True:
        status = scan_registered_projects(due_only=True)
        notify_review_index(status)
        if once:
            return
        time.sleep(interval_seconds)

