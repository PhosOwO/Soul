from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from soul.services.reme.reme_transition import propose_reme_transition
from soul.services.state_core.state_store import state_paths, utc_now

QUEUE_DIR_NAME = "queue"
JOBS_FILE_NAME = "jobs.jsonl"
EVENTS_FILE_NAME = "events.jsonl"
LOCK_FILE_NAME = "worker.lock"

JOB_TYPE_TURN_EVIDENCE = "turn_evidence"
EVENT_QUEUED = "queued"
EVENT_STARTED = "started"
EVENT_COMPLETED = "completed"
EVENT_FAILED = "failed"
EVENT_BLOCKED = "blocked"
EVENT_DEAD_LETTER = "dead_letter"

MAX_ATTEMPTS = 3
STALE_STARTED_AFTER_SECONDS = 300
LOCK_STALE_AFTER_SECONDS = 300
APPEND_LOCK_TIMEOUT_SECONDS = 5.0
APPEND_LOCK_POLL_SECONDS = 0.01
_APPEND_LOCKS_GUARD = threading.Lock()
_APPEND_LOCKS: dict[Path, threading.Lock] = {}

QueueEventName = Literal["queued", "started", "completed", "failed", "blocked", "dead_letter"]


@dataclass(frozen=True, slots=True)
class QueuePaths:
    root: Path
    jobs_path: Path
    events_path: Path
    lock_path: Path


@dataclass(frozen=True, slots=True)
class QueueSelection:
    job: dict[str, Any]
    attempt: int


@dataclass(frozen=True, slots=True)
class QueueSummary:
    queued: int
    started: int
    completed: int
    failed_retryable: int
    blocked: int
    dead_letter: int
    last_completed: dict[str, Any] | None
    last_error: dict[str, Any] | None


def queue_paths(project_dir: Path | None = None) -> QueuePaths:
    root = state_paths(project_dir).state_path.parent / QUEUE_DIR_NAME
    return QueuePaths(
        root=root,
        jobs_path=root / JOBS_FILE_NAME,
        events_path=root / EVENTS_FILE_NAME,
        lock_path=root / LOCK_FILE_NAME,
    )


def enqueue_turn_evidence(
    project_dir: Path,
    *,
    source: str,
    session_id: str,
    turn_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    paths = queue_paths(project_dir)
    paths.root.mkdir(parents=True, exist_ok=True)
    idempotency_key = f"{source}:{session_id}:{turn_id}"
    job = {
        "job_id": "turn_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8],
        "type": JOB_TYPE_TURN_EVIDENCE,
        "created_at": utc_now(),
        "source": source,
        "session_id": session_id,
        "turn_id": turn_id,
        "idempotency_key": idempotency_key,
        "payload": payload,
    }
    append_jsonl(paths.jobs_path, job)
    append_queue_event(project_dir, job["job_id"], EVENT_QUEUED, idempotency_key=idempotency_key)
    return job


def append_queue_event(
    project_dir: Path,
    job_id: str,
    event: QueueEventName,
    **fields: Any,
) -> dict[str, Any]:
    paths = queue_paths(project_dir)
    paths.root.mkdir(parents=True, exist_ok=True)
    record = {
        "job_id": job_id,
        "event": event,
        "created_at": utc_now(),
        **fields,
    }
    append_jsonl(paths.events_path, record)
    return record


def drain_queue(project_dir: Path, *, limit: int = 3) -> dict[str, Any]:
    processed: list[dict[str, Any]] = []
    with queue_lock(project_dir) as acquired:
        if not acquired:
            return {"processed": processed, "locked": True}
        state = replay_queue_state(project_dir)
        for selection in select_runnable_jobs(state, now=datetime.now(UTC), limit=limit):
            result = process_queue_job(project_dir, selection)
            processed.append(result)
    return {"processed": processed, "locked": False}


def process_queue_job(project_dir: Path, selection: QueueSelection) -> dict[str, Any]:
    job = selection.job
    job_id = str(job["job_id"])
    idempotency_key = str(job.get("idempotency_key") or job_id)
    append_queue_event(project_dir, job_id, EVENT_STARTED, attempt=selection.attempt, idempotency_key=idempotency_key)
    try:
        if job.get("type") != JOB_TYPE_TURN_EVIDENCE:
            append_queue_event(
                project_dir,
                job_id,
                EVENT_DEAD_LETTER,
                attempt=selection.attempt,
                idempotency_key=idempotency_key,
                error=f"unsupported job type: {job.get('type')}",
            )
            return {"job_id": job_id, "status": EVENT_DEAD_LETTER}

        payload = job.get("payload")
        if not isinstance(payload, dict):
            append_queue_event(
                project_dir,
                job_id,
                EVENT_DEAD_LETTER,
                attempt=selection.attempt,
                idempotency_key=idempotency_key,
                error="payload must be an object",
            )
            return {"job_id": job_id, "status": EVENT_DEAD_LETTER}

        task = str(payload.get("task") or "")
        outcome = str(payload.get("outcome") or payload.get("summary") or "")
        if not (task or outcome):
            append_queue_event(
                project_dir,
                job_id,
                EVENT_DEAD_LETTER,
                attempt=selection.attempt,
                idempotency_key=idempotency_key,
                error="turn_evidence requires task or outcome",
            )
            return {"job_id": job_id, "status": EVENT_DEAD_LETTER}

        evidence = dict(payload)
        evidence.setdefault("source", job.get("source") or "queue")
        evidence.setdefault("session_id", job.get("session_id") or "")
        evidence["queue"] = {
            "job_id": job_id,
            "idempotency_key": idempotency_key,
        }
        result = propose_reme_transition(
            project_dir=project_dir,
            evidence=evidence,
            episode={
                "task": task,
                "outcome": outcome,
                "session_id": job.get("session_id") or "",
                "events": payload.get("events", []),
                "messages": payload.get("messages", []),
            },
            reme=payload.get("reme") if isinstance(payload.get("reme"), dict) else None,
        )
        working_state = result.get("working_state")
        working_item = working_state.get("item") if isinstance(working_state, dict) else None
        working_state_id = working_item.get("id") if isinstance(working_item, dict) else None
        append_queue_event(
            project_dir,
            job_id,
            EVENT_COMPLETED,
            attempt=selection.attempt,
            idempotency_key=idempotency_key,
            working_state_id=working_state_id,
            working_state_route=working_state.get("route") if isinstance(working_state, dict) else None,
            memory_mode=result.get("memory_mode"),
            reme_write_mode=result.get("reme_write_mode"),
        )
        return {"job_id": job_id, "status": EVENT_COMPLETED, "working_state_id": working_state_id}
    except Exception as exc:
        retryable = is_retryable_error(exc)
        event: QueueEventName = EVENT_FAILED if retryable and selection.attempt < MAX_ATTEMPTS else EVENT_BLOCKED
        append_queue_event(
            project_dir,
            job_id,
            event,
            attempt=selection.attempt,
            idempotency_key=idempotency_key,
            error=compact_error(exc),
            retryable=retryable,
            next_run_at=next_run_at(selection.attempt) if retryable and selection.attempt < MAX_ATTEMPTS else None,
        )
        return {"job_id": job_id, "status": event, "error": compact_error(exc)}


def select_runnable_jobs(queue_state: dict[str, Any], *, now: datetime, limit: int) -> list[QueueSelection]:
    selected: list[QueueSelection] = []
    completed_keys = {
        str(event.get("idempotency_key"))
        for event in queue_state["events"]
        if event.get("event") == EVENT_COMPLETED and event.get("idempotency_key")
    }
    for job in queue_state["jobs"]:
        job_id = str(job.get("job_id") or "")
        idempotency_key = str(job.get("idempotency_key") or job_id)
        if idempotency_key in completed_keys:
            continue
        status = queue_state["status_by_job"].get(job_id, {})
        event = status.get("event", EVENT_QUEUED)
        if event in {EVENT_COMPLETED, EVENT_BLOCKED, EVENT_DEAD_LETTER}:
            continue
        if event == EVENT_STARTED and not is_started_stale(status, now):
            continue
        if event == EVENT_FAILED:
            if not status.get("retryable", False):
                continue
            raw_next_run_at = status.get("next_run_at")
            if raw_next_run_at and parse_time(str(raw_next_run_at)) > now:
                continue
        selected.append(QueueSelection(job=job, attempt=int(status.get("attempt") or 0) + 1))
        if len(selected) >= limit:
            break
    return selected


def queue_status(project_dir: Path) -> QueueSummary:
    state = replay_queue_state(project_dir)
    completed_keys: set[str] = set()
    counts = {
        "queued": 0,
        "started": 0,
        "completed": 0,
        "failed_retryable": 0,
        "blocked": 0,
        "dead_letter": 0,
    }
    for event in state["events"]:
        if event.get("event") == EVENT_COMPLETED and event.get("idempotency_key"):
            completed_keys.add(str(event["idempotency_key"]))
    for job in state["jobs"]:
        job_id = str(job.get("job_id") or "")
        idempotency_key = str(job.get("idempotency_key") or job_id)
        if idempotency_key in completed_keys:
            counts["completed"] += 1
            continue
        status = state["status_by_job"].get(job_id, {})
        event = status.get("event", EVENT_QUEUED)
        if event == EVENT_FAILED and status.get("retryable", False):
            counts["failed_retryable"] += 1
        elif event in counts:
            counts[cast(str, event)] += 1
        else:
            counts["queued"] += 1

    last_completed = next((event for event in reversed(state["events"]) if event.get("event") == EVENT_COMPLETED), None)
    last_error = next(
        (
            event
            for event in reversed(state["events"])
            if event.get("event") in {EVENT_FAILED, EVENT_BLOCKED, EVENT_DEAD_LETTER}
        ),
        None,
    )
    return QueueSummary(
        queued=counts["queued"],
        started=counts["started"],
        completed=counts["completed"],
        failed_retryable=counts["failed_retryable"],
        blocked=counts["blocked"],
        dead_letter=counts["dead_letter"],
        last_completed=last_completed,
        last_error=last_error,
    )


def replay_queue_state(project_dir: Path) -> dict[str, Any]:
    paths = queue_paths(project_dir)
    jobs = read_jsonl(paths.jobs_path)
    events = read_jsonl(paths.events_path)
    status_by_job: dict[str, dict[str, Any]] = {}
    for event in events:
        job_id = event.get("job_id")
        if isinstance(job_id, str):
            status_by_job[job_id] = event
    return {"jobs": jobs, "events": events, "status_by_job": status_by_job}


class queue_lock:
    def __init__(self, project_dir: Path) -> None:
        self.paths = queue_paths(project_dir)
        self.fd: int | None = None

    def __enter__(self) -> bool:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        if self.paths.lock_path.exists() and is_lock_stale(self.paths.lock_path):
            try:
                self.paths.lock_path.unlink()
            except OSError:
                return False
        try:
            self.fd = os.open(str(self.paths.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, json.dumps({"pid": os.getpid(), "created_at": utc_now()}).encode("utf-8"))
            return True
        except FileExistsError:
            return False

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.paths.lock_path.unlink()
        except OSError:
            return


def is_lock_stale(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    created_at = parse_time(str(data.get("created_at") or ""))
    return datetime.now(UTC) - created_at > timedelta(seconds=LOCK_STALE_AFTER_SECONDS)


def is_started_stale(status: dict[str, Any], now: datetime) -> bool:
    created_at = parse_time(str(status.get("created_at") or ""))
    return now - created_at > timedelta(seconds=STALE_STARTED_AFTER_SECONDS)


def next_run_at(attempt: int) -> str:
    delay_seconds = min(300, 15 * (2 ** max(0, attempt - 1)))
    return (datetime.now(UTC) + timedelta(seconds=delay_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)


def is_retryable_error(exc: Exception) -> bool:
    text = str(exc).lower()
    blocked_markers = ["missing credentials", "api_key", "openai_api_key", "workload_identity", "not found", "no such file"]
    if any(marker in text for marker in blocked_markers):
        return False
    return True


def stable_turn_id(payload: dict[str, Any]) -> str:
    for key in ("turn_id", "message_id", "request_id"):
        if payload.get(key):
            return str(payload[key])
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return "turn-" + digest


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with process_append_lock(path), jsonl_append_lock(path), path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def process_append_lock(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _APPEND_LOCKS_GUARD:
        lock = _APPEND_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _APPEND_LOCKS[resolved] = lock
        return lock


class jsonl_append_lock:
    def __init__(self, path: Path) -> None:
        self.lock_path = path.with_name(path.name + ".lock")
        self.fd: int | None = None

    def __enter__(self) -> None:
        deadline = time.monotonic() + APPEND_LOCK_TIMEOUT_SECONDS
        while True:
            if self.lock_path.exists() and is_lock_stale(self.lock_path):
                try:
                    self.lock_path.unlink()
                except OSError:
                    pass
            try:
                self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, json.dumps({"pid": os.getpid(), "created_at": utc_now()}).encode("utf-8"))
                return
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for queue JSONL append lock: {self.lock_path}")
                time.sleep(APPEND_LOCK_POLL_SECONDS)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.lock_path.unlink()
        except OSError:
            return


def compact_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]
