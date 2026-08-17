from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def main() -> None:
    payload = read_payload()
    project_dir = Path(payload.get("cwd") or Path.cwd()).resolve()
    prompt = str(payload.get("prompt") or "")

    add_soul_package_path(project_dir)
    from soul.api import SoulApi, build_agent_injection

    try:
        state = SoulApi(project_dir).get_state(task=prompt, limit=8)
    except Exception as exc:
        append_hook_run(
            project_dir,
            payload,
            {
                "status": "error",
                "injected": False,
                "error": compact_error(exc),
            },
        )
        write_json(
            {
                "systemMessage": f"Soul Current State unavailable: {exc}",
                "suppressOutput": False,
            }
        )
        return

    state_artifact = project_dir / ".soul" / "state" / "STATE.md"
    append_hook_run(
        project_dir,
        payload,
        {
            "status": "success",
            "injected": True,
            "state_artifact": ".soul/state/STATE.md" if state_artifact.exists() else None,
        },
    )
    write_json(
        {
            "suppressOutput": True,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": build_agent_injection(str(state.get("context", ""))),
            },
        }
    )


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def append_hook_run(project_dir: Path, payload: dict[str, Any], fields: dict[str, Any]) -> None:
    record = {
        "created_at": datetime.now(UTC).isoformat(),
        "hook_event_name": payload.get("hook_event_name") or payload.get("hookEventName") or "UserPromptSubmit",
        "project_dir": str(project_dir),
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "thread_name": payload.get("thread_name") or payload.get("session_name"),
        **fields,
    }
    compact = {key: value for key, value in record.items() if value is not None}
    try:
        path = project_dir / ".soul" / "state" / "hook_runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(compact, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return


def compact_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]


def add_soul_package_path(project_dir: Path) -> None:
    candidates: list[Path] = []
    configured = os.environ.get("SOULKIT_HOME")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(project_dir / "node_modules" / "@soulkit" / "soul")
    candidates.append(Path(__file__).resolve().parents[2])

    for candidate in candidates:
        if (candidate / "soul" / "api.py").exists():
            sys.path.insert(0, str(candidate))
            return

    sys.path.insert(0, str(project_dir))


def write_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
