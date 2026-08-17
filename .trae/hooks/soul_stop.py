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
    prompt = extract_prompt(payload)
    outcome = str(payload.get("last_assistant_message") or "")
    session_id = str(payload.get("session_id") or payload.get("turn_id") or "traex-session")

    if not (prompt or outcome):
        append_hook_run(
            project_dir,
            payload,
            {
                "status": "success",
                "recorded": False,
                "reason": "empty_prompt_and_outcome",
            },
        )
        write_json({"suppressOutput": True})
        return

    add_soul_package_path(project_dir)
    from soul.api import SoulApi

    try:
        result = SoulApi(project_dir).propose_reme_transition(
            evidence={
                "source": "traex",
                "task": prompt,
                "outcome": outcome,
                "summary": outcome,
                "session_id": session_id,
                "events": [compact_hook_payload(payload)],
                "messages": normalized_messages(prompt, outcome),
            },
            episode={
                "task": prompt,
                "outcome": outcome,
                "session_id": session_id,
                "events": [compact_hook_payload(payload)],
                "messages": normalized_messages(prompt, outcome),
            },
            reme={
                "write_mode": "auto_memory",
                "memory_hint": "Preserve durable TraeX project decisions, constraints, procedures, preferences, and evidence links.",
            },
        )
    except Exception as exc:
        append_hook_run(
            project_dir,
            payload,
            {
                "status": "error",
                "recorded": False,
                "error": compact_error(exc),
            },
        )
        write_json(
            {
                "systemMessage": f"Soul evidence was not recorded: {exc}",
                "suppressOutput": False,
            }
        )
        return

    patch = result.get("patch_proposal", {})
    evidence_refs = result.get("evidence_refs", [])
    append_hook_run(
        project_dir,
        payload,
        {
            "status": "success",
            "recorded": True,
            "patch_id": patch.get("id"),
            "memory_mode": result.get("memory_mode"),
            "reme_write_mode": result.get("reme_write_mode"),
            "reme_requested_write_mode": result.get("reme_requested_write_mode"),
            "evidence_ref_count": len(evidence_refs) if isinstance(evidence_refs, list) else 0,
            "trace_path": result.get("trace_path"),
        },
    )
    write_json(
        {
            "suppressOutput": True,
            "systemMessage": f"Soul proposed state patch {patch.get('id', 'unknown')} from TraeX evidence.",
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
        "hook_event_name": payload.get("hook_event_name") or payload.get("hookEventName") or "Stop",
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


def extract_prompt(payload: dict[str, Any]) -> str:
    direct = payload.get("prompt")
    if direct:
        return str(direct)
    nested = payload.get("user_prompt_submit")
    if isinstance(nested, dict) and nested.get("prompt"):
        return str(nested["prompt"])
    return ""


def normalized_messages(prompt: str, outcome: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if prompt:
        messages.append({"role": "user", "content": prompt})
    if outcome:
        messages.append({"role": "assistant", "content": outcome})
    return messages


def compact_hook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "hook_event_name",
        "session_id",
        "thread_name",
        "turn_id",
        "cwd",
        "model",
        "permission_mode",
    ]
    return {key: payload[key] for key in keys if payload.get(key) is not None}


def write_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
