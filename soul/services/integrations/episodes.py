from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from soul.services.state_core.state_store import brain_dir, utc_now

EPISODES_FILE_NAME = "episodes.jsonl"
EPISODE_EVENTS_FILE_NAME = "episode_events.jsonl"
EPISODE_VIEWS_FILE_NAME = "episode_views.jsonl"
EPISODE_ARTIFACTS_DIR_NAME = "episode_artifacts"
SYSTEM_EVENTS_FILE_NAME = "system_events.jsonl"
NOISE_MARKERS = (
    "<system-reminder>",
    "<available_skills>",
    "<environment_context>",
    "<permissions instructions>",
    "permissions instructions",
)


@dataclass(frozen=True, slots=True)
class EpisodePaths:
    episodes_path: Path
    episode_events_path: Path
    episode_views_path: Path
    episode_artifacts_dir: Path
    system_events_path: Path


def episode_paths(project_dir: Path | None = None) -> EpisodePaths:
    root = brain_dir(project_dir)
    return EpisodePaths(
        episodes_path=root / EPISODES_FILE_NAME,
        episode_events_path=root / EPISODE_EVENTS_FILE_NAME,
        episode_views_path=root / EPISODE_VIEWS_FILE_NAME,
        episode_artifacts_dir=root / EPISODE_ARTIFACTS_DIR_NAME,
        system_events_path=root / SYSTEM_EVENTS_FILE_NAME,
    )


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            records.append(payload)
    return records


def read_episodes(project_dir: Path | None = None) -> list[dict[str, Any]]:
    return compact_latest_by_id(read_jsonl(episode_paths(project_dir).episodes_path))


def read_episode_events(project_dir: Path | None = None) -> list[dict[str, Any]]:
    return read_jsonl(episode_paths(project_dir).episode_events_path)


def read_episode_views(project_dir: Path | None = None) -> list[dict[str, Any]]:
    return compact_latest_by_id(read_jsonl(episode_paths(project_dir).episode_views_path))


def find_episode(project_dir: Path | None, episode_id: str) -> dict[str, Any] | None:
    for episode in reversed(read_episodes(project_dir)):
        if str(episode.get("id") or "") == str(episode_id):
            return episode
    return None


def find_episode_view(project_dir: Path | None, episode_id: str) -> dict[str, Any] | None:
    for episode in reversed(read_episode_views(project_dir)):
        if str(episode.get("id") or episode.get("episode_id") or "") == str(episode_id):
            return episode
    return None


def resolve_episode_selector(project_dir: Path | None, selector: str) -> dict[str, Any] | None:
    episodes = read_episodes(project_dir)
    if selector.isdigit():
        index = int(selector)
        if 1 <= index <= len(episodes):
            return episodes[index - 1]
    return find_episode(project_dir, selector)


def new_episode_id() -> str:
    return "episode_" + uuid4().hex


def stable_episode_id(*, source: str, session_id: str, turn_id: str) -> str:
    return f"{sanitize_id(source)}:{sanitize_id(session_id)}:{sanitize_id(turn_id)}"


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def append_episode_event(project_dir: Path | None, event: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    normalized = normalize_episode_event(project_dir, event)
    for existing in read_episode_events(project_dir):
        if str(existing.get("event_id") or "") == normalized["event_id"]:
            return existing, False
        if normalized.get("idempotency_key") and existing.get("idempotency_key") == normalized["idempotency_key"]:
            return existing, False
    append_jsonl(episode_paths(project_dir).episode_events_path, normalized)
    return normalized, True


def normalize_episode_event(project_dir: Path | None, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else None
    event_id = str(event.get("event_id") or "evt_" + uuid4().hex)
    episode_id = str(event.get("episode_id") or "")
    if not episode_id:
        episode_id = stable_episode_id(
            source=str(event.get("host") or event.get("source") or "unknown"),
            session_id=str(event.get("session_id") or "session"),
            turn_id=str(event.get("turn_id") or event_id),
        )
    normalized = {
        "schema_version": int(event.get("schema_version") or 1),
        "event_id": event_id,
        "episode_id": episode_id,
        "episode_version_hint": int(event.get("episode_version_hint") or 0),
        "event_type": str(event.get("event_type") or "turn_completed"),
        "host": str(event.get("host") or event.get("source") or "unknown"),
        "project_id": str(event.get("project_id") or ""),
        "session_id": str(event.get("session_id") or ""),
        "idempotency_key": str(event.get("idempotency_key") or event_id),
        "created_at": str(event.get("created_at") or utc_now()),
    }
    payload_ref = str(event.get("payload_ref") or "")
    if payload is not None:
        payload_ref = write_episode_artifact(project_dir, episode_id, f"{event_id}.json", payload)
        normalized["payload_hash"] = content_hash(payload)
    elif event.get("payload_hash"):
        normalized["payload_hash"] = str(event["payload_hash"])
    if payload_ref:
        normalized["payload_ref"] = payload_ref
    return normalized


def episode_event_from_turn_evidence(
    *,
    source: str,
    session_id: str,
    turn_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    episode_id = str(payload.get("episode_id") or stable_episode_id(source=source, session_id=session_id, turn_id=turn_id))
    return {
        "schema_version": 1,
        "event_id": "evt_" + hashlib.sha1(f"{source}:{session_id}:{turn_id}:turn_completed".encode("utf-8")).hexdigest()[:16],
        "episode_id": episode_id,
        "event_type": "turn_completed",
        "host": source,
        "project_id": str(payload.get("project_id") or ""),
        "session_id": session_id,
        "turn_id": turn_id,
        "idempotency_key": f"{source}:{session_id}:{turn_id}:turn_completed",
        "payload": {
            "task": {
                "text": str(payload.get("task") or ""),
                "source": str(payload.get("task_source") or payload.get("source") or source),
                "external_id": str(payload.get("external_id") or ""),
            },
            "conversation": {
                "messages": payload.get("messages", []) if isinstance(payload.get("messages"), list) else [],
            },
            "execution": {
                "status": str(payload.get("status") or "completed"),
                "duration_ms": int(payload.get("duration_ms") or 0),
                "commands": payload.get("commands", []) if isinstance(payload.get("commands"), list) else [],
                "tool_calls": payload.get("tool_calls", []) if isinstance(payload.get("tool_calls"), list) else [],
            },
            "outcome": str(payload.get("outcome") or payload.get("summary") or ""),
            "summary": str(payload.get("summary") or payload.get("outcome") or ""),
            **optional_payload_fields(payload, ["working_state", "knowledge_points", "state_item", "state_items", "reme"]),
        },
    }


def append_episode(project_dir: Path | None, episode: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "id": new_episode_id(),
        **episode,
        "record_type": "episode",
    }
    append_jsonl(episode_paths(project_dir).episodes_path, payload)
    return payload


def upsert_episode_by_source_path(project_dir: Path | None, episode: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    source = str(episode.get("source") or "")
    source_path = str(episode.get("source_path") or "")
    existing = None
    if source and source_path:
        for candidate in reversed(read_episodes(project_dir)):
            if candidate.get("source") == source and candidate.get("source_path") == source_path:
                existing = candidate
                break
    if existing is None:
        return append_episode(project_dir, episode), True

    updated = {
        **existing,
        **episode,
        "id": existing["id"],
        "created_at": existing.get("created_at") or utc_now(),
        "updated_at": utc_now(),
    }
    append_jsonl(episode_paths(project_dir).episodes_path, {**updated, "record_type": "episode_update"})
    return updated, False


def append_system_event(
    project_dir: Path | None,
    *,
    event_type: str,
    data: dict[str, Any],
    reason: str,
    source: str,
) -> dict[str, Any]:
    payload = {
        "created_at": utc_now(),
        "type": event_type,
        "data": data,
        "reason": reason,
        "source": source,
    }
    append_jsonl(episode_paths(project_dir).system_events_path, payload)
    return payload


def read_system_events(project_dir: Path | None = None) -> list[dict[str, Any]]:
    return read_jsonl(episode_paths(project_dir).system_events_path)


def compact_latest_by_id(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        raw_id = record.get("id")
        if raw_id is None:
            continue
        episode_id = str(raw_id)
        if episode_id not in by_id:
            order.append(episode_id)
        by_id[episode_id] = record
    return [by_id[episode_id] for episode_id in order]


def assemble_episode_view(project_dir: Path | None, episode_id: str) -> dict[str, Any]:
    events = [event for event in read_episode_events(project_dir) if str(event.get("episode_id") or "") == episode_id]
    if not events:
        raise ValueError(f"Episode events not found: {episode_id}")

    view: dict[str, Any] = {
        "schema_version": 1,
        "id": episode_id,
        "episode_id": episode_id,
        "episode_version": 0,
        "host": "",
        "session_id": "",
        "source": "episode_event",
        "project": {"root": str(project_dir) if project_dir is not None else "", "id": "", "repo": ""},
        "task": {"text": "", "source": "unknown", "external_id": ""},
        "conversation": {"messages_ref": "", "summary": ""},
        "execution": {"status": "unknown", "duration_ms": 0, "commands_ref": "", "tool_calls_ref": ""},
        "artifacts": {"patch_ref": "", "patch_hash": "", "changed_files": [], "logs": []},
        "verification": {"verifier": "none", "status": "unknown", "details_ref": ""},
        "candidate_inputs": {},
        "event_refs": [],
        "ready_for_reflection": False,
        "updated_at": utc_now(),
        "record_type": "episode_view",
    }
    semantic_hash_parts: list[str] = []
    for event in sorted(events, key=lambda item: str(item.get("created_at") or "")):
        view["event_refs"].append(event["event_id"])
        view["host"] = first_non_empty(event.get("host"), view.get("host"), default="unknown")
        view["session_id"] = first_non_empty(event.get("session_id"), view.get("session_id"))
        view["project"]["id"] = first_non_empty(view["project"].get("id"), event.get("project_id"))
        payload = read_episode_event_payload(project_dir, event)
        merge_event_payload(project_dir, view, payload)
        semantic_hash_parts.append(str(event.get("event_id") or ""))
        semantic_hash_parts.append(str(event.get("payload_hash") or ""))

    view["conversation"]["summary"] = compact_episode_summary(view)
    view["episode_version"] = max(1, int(hashlib.sha1("|".join(semantic_hash_parts).encode("utf-8")).hexdigest()[:8], 16))
    view["ready_for_reflection"] = is_episode_ready_for_reflection(view)
    append_jsonl(episode_paths(project_dir).episode_views_path, view)
    append_jsonl(episode_paths(project_dir).episodes_path, legacy_episode_from_view(view))
    return view


def read_episode_event_payload(project_dir: Path | None, event: dict[str, Any]) -> dict[str, Any]:
    direct = event.get("payload")
    if isinstance(direct, dict):
        return direct
    payload_ref = str(event.get("payload_ref") or "")
    if not payload_ref:
        return {}
    path = path_from_ref(project_dir, payload_ref)
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_event_payload(project_dir: Path | None, view: dict[str, Any], payload: dict[str, Any]) -> None:
    project = payload.get("project")
    if isinstance(project, dict):
        view["project"]["root"] = first_non_empty(project.get("root"), view["project"].get("root"))
        view["project"]["repo"] = first_non_empty(project.get("repo"), view["project"].get("repo"))
        view["project"]["id"] = first_non_empty(project.get("id"), view["project"].get("id"))
    task = payload.get("task")
    if isinstance(task, dict):
        view["task"]["text"] = first_non_empty(task.get("text"), view["task"].get("text"))
        view["task"]["source"] = first_non_empty(task.get("source"), view["task"].get("source"), default="unknown")
        view["task"]["external_id"] = first_non_empty(task.get("external_id"), view["task"].get("external_id"))
    conversation = payload.get("conversation")
    if isinstance(conversation, dict):
        raw_messages = conversation.get("messages")
        messages = filter_noise_messages(raw_messages if isinstance(raw_messages, list) else [])
        if messages:
            view["conversation"]["messages_ref"] = write_episode_artifact(project_dir, str(view["episode_id"]), "messages.json", messages)
        view["conversation"]["summary"] = first_non_empty(conversation.get("summary"), view["conversation"].get("summary"))
    execution = payload.get("execution")
    if isinstance(execution, dict):
        for key in ("status", "duration_ms", "commands_ref", "tool_calls_ref"):
            if execution.get(key) not in (None, "", []):
                view["execution"][key] = execution[key]
        if isinstance(execution.get("commands"), list):
            view["execution"]["commands_ref"] = write_episode_artifact(project_dir, str(view["episode_id"]), "commands.json", execution["commands"])
        if isinstance(execution.get("tool_calls"), list):
            view["execution"]["tool_calls_ref"] = write_episode_artifact(project_dir, str(view["episode_id"]), "tool_calls.json", execution["tool_calls"])
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        if artifacts.get("patch_ref"):
            view["artifacts"]["patch_ref"] = artifacts["patch_ref"]
        if artifacts.get("patch_hash"):
            view["artifacts"]["patch_hash"] = artifacts["patch_hash"]
        if artifacts.get("patch"):
            patch_text = str(artifacts["patch"])
            view["artifacts"]["patch_ref"] = write_episode_artifact(project_dir, str(view["episode_id"]), "patch.diff", patch_text)
            view["artifacts"]["patch_hash"] = content_hash(patch_text)
        if isinstance(artifacts.get("changed_files"), list):
            view["artifacts"]["changed_files"] = merge_scalars(view["artifacts"].get("changed_files", []), artifacts["changed_files"])
        if isinstance(artifacts.get("logs"), list):
            view["artifacts"]["logs"] = merge_scalars(view["artifacts"].get("logs", []), artifacts["logs"])
    verification = payload.get("verification")
    if isinstance(verification, dict):
        for key in ("verifier", "status", "details_ref"):
            if verification.get(key):
                view["verification"][key] = verification[key]
        if isinstance(verification.get("details"), dict):
            view["verification"]["details_ref"] = write_episode_artifact(project_dir, str(view["episode_id"]), "verification.json", verification["details"])
    for key in ("working_state", "knowledge_points", "state_item", "state_items", "reme"):
        if key in payload:
            view["candidate_inputs"][key] = payload[key]
    if payload.get("summary"):
        view["conversation"]["summary"] = str(payload["summary"])
    if payload.get("outcome"):
        view["outcome"] = str(payload["outcome"])


def is_episode_ready_for_reflection(view: dict[str, Any]) -> bool:
    status = str(view.get("execution", {}).get("status") or "")
    return status in {"completed", "failed", "interrupted"}


def evidence_from_episode_view(view: dict[str, Any]) -> dict[str, Any]:
    task = str(view.get("task", {}).get("text") or "")
    outcome = str(view.get("outcome") or view.get("conversation", {}).get("summary") or "")
    evidence = {
        "source": f"episode:{view.get('episode_id')}",
        "task": task,
        "outcome": outcome,
        "summary": outcome or task,
        "content": outcome,
        "episode_id": view.get("episode_id"),
        "episode_version": view.get("episode_version"),
        "evidence_refs": [
            {"kind": "episode_view", "episode_id": view.get("episode_id"), "version": view.get("episode_version")},
            *artifact_evidence_refs(view),
        ],
        "episode_view": view,
    }
    raw_candidate_inputs = view.get("candidate_inputs")
    candidate_inputs: dict[str, Any] = raw_candidate_inputs if isinstance(raw_candidate_inputs, dict) else {}
    evidence.update(candidate_inputs)
    return evidence


def artifact_evidence_refs(view: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    raw_conversation = view.get("conversation")
    conversation: dict[str, Any] = raw_conversation if isinstance(raw_conversation, dict) else {}
    raw_artifacts = view.get("artifacts")
    artifacts: dict[str, Any] = raw_artifacts if isinstance(raw_artifacts, dict) else {}
    raw_verification = view.get("verification")
    verification: dict[str, Any] = raw_verification if isinstance(raw_verification, dict) else {}
    for kind, value in (
        ("messages", conversation.get("messages_ref")),
        ("patch", artifacts.get("patch_ref")),
        ("verification", verification.get("details_ref")),
    ):
        if value:
            refs.append({"kind": kind, "path": str(value)})
    return refs


def legacy_episode_from_view(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": view["episode_id"],
        "record_type": "episode_update",
        "source": view.get("source") or view.get("host") or "episode_event",
        "summary": view.get("conversation", {}).get("summary") or view.get("outcome") or view.get("task", {}).get("text") or "",
        "source_path": view.get("episode_id"),
        "metadata": {
            "episode_version": view.get("episode_version"),
            "project": view.get("project"),
            "execution": view.get("execution"),
            "artifacts": view.get("artifacts"),
            "verification": view.get("verification"),
            "event_refs": view.get("event_refs", []),
        },
        "messages": [],
        "updated_at": utc_now(),
    }


def compact_episode_summary(view: dict[str, Any], max_length: int = 500) -> str:
    existing = str(view.get("conversation", {}).get("summary") or view.get("outcome") or "")
    if existing.strip():
        return compact_text(existing, max_length)
    task = str(view.get("task", {}).get("text") or "")
    status = str(view.get("verification", {}).get("status") or view.get("execution", {}).get("status") or "unknown")
    return compact_text(f"{status}: {task}", max_length)


def write_episode_artifact(project_dir: Path | None, episode_id: str, name: str, payload: Any) -> str:
    paths = episode_paths(project_dir)
    safe_episode = sanitize_id(episode_id) or "episode"
    safe_name = sanitize_filename(name)
    path = paths.episode_artifacts_dir / safe_episode / safe_name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        content = payload
    else:
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8")
    return str(path.relative_to(brain_dir(project_dir))).replace("\\", "/")


def path_from_ref(project_dir: Path | None, ref: str) -> Path | None:
    cleaned = ref.removeprefix("blob://")
    if cleaned.startswith("episodes/"):
        parts = cleaned.split("/", 1)
        cleaned = f"{EPISODE_ARTIFACTS_DIR_NAME}/{parts[1]}" if len(parts) == 2 else cleaned
    path = Path(cleaned)
    if path.is_absolute():
        return path
    return brain_dir(project_dir) / path


def filter_noise_messages(messages: list[Any]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        text = str(message.get("content") or message.get("text") or "")
        lowered = text.lower()
        role = str(message.get("role") or "")
        if role == "system" or any(marker in lowered for marker in NOISE_MARKERS):
            continue
        filtered.append(message)
    return filtered


def merge_scalars(left: Any, right: Any) -> list[Any]:
    values: list[Any] = []
    seen: set[str] = set()
    for item in list(left if isinstance(left, list) else []) + list(right if isinstance(right, list) else []):
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        values.append(item)
    return values


def optional_payload_fields(payload: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload}


def first_non_empty(*values: Any, default: str = "") -> str:
    for value in values:
        if value not in (None, "", []):
            return str(value)
    return default


def compact_text(text: object, max_length: int) -> str:
    one_line = " ".join(str(text or "").split())
    if len(one_line) <= max_length:
        return one_line
    return one_line[: max_length - 3].rstrip() + "..."


def sanitize_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip()).strip("-")[:160]


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return cleaned or "payload.json"
