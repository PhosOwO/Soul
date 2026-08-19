from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def append_integration_run(project_dir: Path, record: dict[str, Any]) -> None:
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        **record,
    }
    compact = {key: value for key, value in payload.items() if value is not None}
    path = project_dir / ".soul" / "state" / "integration_runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(compact, ensure_ascii=False, sort_keys=True) + "\n")


def read_integration_runs(project_dir: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    path = project_dir / ".soul" / "state" / "integration_runs.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
        if len(records) >= limit:
            break
    return records


def latest_matching_run(records: list[dict[str, Any]], *, host_prefix: str) -> dict[str, Any] | None:
    for record in records:
        host = str(record.get("host") or "")
        if host == host_prefix or host.startswith(f"{host_prefix}:"):
            return record
    return None
