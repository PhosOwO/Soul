from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal


HookHost = Literal["traex", "codex", "dsh", "generic"]
HookEvent = Literal["UserPromptSubmit", "Stop"]


@dataclass(frozen=True, slots=True)
class HookResult:
    output: dict[str, Any]
    project_dir: Path
    heartbeat: dict[str, Any]


def run_user_prompt_submit_hook(payload: dict[str, Any], *, host: HookHost) -> HookResult:
    project_dir = project_dir_from_payload(payload)
    prompt = extract_prompt(payload)

    try:
        from soul.api import SoulApi, build_agent_injection

        state = SoulApi(project_dir).get_state(task=prompt, limit=8)
        state_artifact = project_dir / ".soul" / "state" / "STATE.md"
        heartbeat = append_hook_run(
            project_dir,
            payload,
            {
                "status": "success",
                "host": host,
                "injected": True,
                "state_artifact": ".soul/state/STATE.md" if state_artifact.exists() else None,
            },
            default_event="UserPromptSubmit",
        )
        return HookResult(
            output={
                "suppressOutput": True,
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": build_agent_injection(str(state.get("context", ""))),
                },
            },
            project_dir=project_dir,
            heartbeat=heartbeat,
        )
    except Exception as exc:
        heartbeat = append_hook_run(
            project_dir,
            payload,
            {
                "status": "error",
                "host": host,
                "injected": False,
                "error": compact_error(exc),
            },
            default_event="UserPromptSubmit",
        )
        return HookResult(
            output={
                "systemMessage": f"Soul Current State unavailable: {exc}",
                "suppressOutput": False,
            },
            project_dir=project_dir,
            heartbeat=heartbeat,
        )


def run_stop_hook(payload: dict[str, Any], *, host: HookHost) -> HookResult:
    project_dir = project_dir_from_payload(payload)
    prompt = extract_prompt(payload)
    outcome = extract_outcome(payload)
    session_id = str(payload.get("session_id") or payload.get("turn_id") or f"{host}-session")

    if not (prompt or outcome):
        heartbeat = append_hook_run(
            project_dir,
            payload,
            {
                "status": "success",
                "host": host,
                "queued": False,
                "reason": "empty_prompt_and_outcome",
            },
            default_event="Stop",
        )
        return HookResult(output={"suppressOutput": True}, project_dir=project_dir, heartbeat=heartbeat)

    from soul.services.integrations.queue import enqueue_turn_evidence, stable_turn_id

    try:
        turn_id = str(payload.get("turn_id") or stable_turn_id(payload))
        job = enqueue_turn_evidence(
            project_dir,
            source=host,
            session_id=session_id,
            turn_id=turn_id,
            payload={
                "source": host,
                "task": prompt,
                "outcome": outcome,
                "summary": outcome,
                "session_id": session_id,
                "turn_id": turn_id,
                "events": [compact_hook_payload(payload)],
                "messages": normalized_messages(prompt, outcome),
                "reme": {
                    "write_mode": "auto_memory",
                    "memory_hint": (
                        f"Preserve durable {host} project decisions, constraints, procedures, "
                        "preferences, and evidence links."
                    ),
                },
            },
        )
    except Exception as exc:
        heartbeat = append_hook_run(
            project_dir,
            payload,
            {
                "status": "error",
                "host": host,
                "queued": False,
                "error": compact_error(exc),
            },
            default_event="Stop",
        )
        return HookResult(
            output={
                "systemMessage": f"Soul evidence was not recorded: {exc}",
                "suppressOutput": False,
            },
            project_dir=project_dir,
            heartbeat=heartbeat,
        )

    worker_started = start_queue_drain(project_dir)
    heartbeat = append_hook_run(
        project_dir,
        payload,
        {
            "status": "success",
            "host": host,
            "queued": True,
            "job_id": job.get("job_id"),
            "worker_started": worker_started,
        },
        default_event="Stop",
    )
    return HookResult(
        output={
            "suppressOutput": True,
            "systemMessage": f"Soul queued evidence job {job.get('job_id', 'unknown')} for background processing.",
        },
        project_dir=project_dir,
        heartbeat=heartbeat,
    )


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def write_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def project_dir_from_payload(payload: dict[str, Any]) -> Path:
    return Path(payload.get("cwd") or payload.get("project_dir") or Path.cwd()).resolve()


def extract_prompt(payload: dict[str, Any]) -> str:
    direct = payload.get("prompt")
    if direct:
        return str(direct)
    nested = payload.get("user_prompt_submit")
    if isinstance(nested, dict) and nested.get("prompt"):
        return str(nested["prompt"])
    return ""


def extract_outcome(payload: dict[str, Any]) -> str:
    for key in ("last_assistant_message", "outcome", "summary", "response"):
        if payload.get(key):
            return str(payload[key])
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
        "hookEventName",
        "session_id",
        "thread_name",
        "session_name",
        "turn_id",
        "cwd",
        "project_dir",
        "model",
        "permission_mode",
    ]
    return {key: payload[key] for key in keys if payload.get(key) is not None}


def append_hook_run(
    project_dir: Path,
    payload: dict[str, Any],
    fields: dict[str, Any],
    *,
    default_event: HookEvent,
) -> dict[str, Any]:
    record = {
        "created_at": datetime.now(UTC).isoformat(),
        "hook_event_name": payload.get("hook_event_name") or payload.get("hookEventName") or default_event,
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
        pass
    return compact


def compact_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]


def start_queue_drain(project_dir: Path) -> bool:
    if os.environ.get("SOUL_DISABLE_BACKGROUND_DRAIN") == "1":
        return False
    env = os.environ.copy()
    package_path = find_soul_package_path(project_dir)
    if package_path is not None:
        python_path = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(package_path) if not python_path else str(package_path) + os.pathsep + python_path
    command = [
        sys.executable or "python3",
        "-m",
        "soul.cli",
        "queue",
        "drain",
        "--project-dir",
        str(project_dir),
        "--limit",
        "3",
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, cwd=str(project_dir), **kwargs)
        return True
    except OSError:
        return False


def find_soul_package_path(project_dir: Path) -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("SOULKIT_HOME")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(project_dir / "node_modules" / "@soulkit" / "soul")
    candidates.append(Path(__file__).resolve().parents[2])

    for candidate in candidates:
        if (candidate / "soul" / "api.py").exists():
            return candidate
    return None
