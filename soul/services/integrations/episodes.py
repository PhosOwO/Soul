from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from soul.services.state_core.state_store import brain_dir, utc_now

EPISODES_FILE_NAME = "episodes.jsonl"
SYSTEM_EVENTS_FILE_NAME = "system_events.jsonl"


@dataclass(frozen=True, slots=True)
class EpisodePaths:
    episodes_path: Path
    system_events_path: Path


def episode_paths(project_dir: Path | None = None) -> EpisodePaths:
    root = brain_dir(project_dir)
    return EpisodePaths(
        episodes_path=root / EPISODES_FILE_NAME,
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


def find_episode(project_dir: Path | None, episode_id: str) -> dict[str, Any] | None:
    for episode in reversed(read_episodes(project_dir)):
        if str(episode.get("id") or "") == str(episode_id):
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
