from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from soul.adapters.reme import ReMeCliAdapter, reme_evidence_refs, reme_write_refs
from soul.services.shared.constants import (
    HOST_SOUL_API,
    MEMORY_MODE_SOUL_REME,
    MEMORY_OWNER_REME,
    OP_PROPOSE_REME_TRANSITION,
    REME_WRITE_MODE_AUTO_MEMORY,
    REME_WRITE_MODE_FALLBACK_DAILY,
    STATE_OWNER_SOUL,
    STATUS_SUCCESS,
)
from soul.services.reme.reme_refs import (
    append_reme_state_trace,
    compact_transition_summary,
    default_reme_workspace,
    merge_evidence_refs,
    safe_reme_note_name,
    safe_reme_session_id,
)
from soul.services.state_core.proposals import propose_patch
from soul.services.state_core.state_store import append_patch_proposal, load_state
from soul.services.state_core.state_store import int_value


def propose_reme_transition(
    *,
    project_dir: Path,
    evidence: dict[str, Any] | None = None,
    episode: dict[str, Any] | None = None,
    reme: dict[str, Any] | None = None,
    record_integration_run: Callable[[dict[str, Any]], None] | None = None,
    adapter_class: Callable[..., Any] = ReMeCliAdapter,
) -> dict[str, Any]:
    evidence_payload = dict(evidence or {})
    episode_payload = episode or {}
    reme_config = reme or {}
    task = str(evidence_payload.get("task") or episode_payload.get("task") or "")
    outcome = str(
        evidence_payload.get("outcome")
        or evidence_payload.get("summary")
        or episode_payload.get("outcome")
        or ""
    )
    fallback_session_id = "soul-agent-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    session_id = str(
        evidence_payload.get("session_id")
        or episode_payload.get("session_id")
        or fallback_session_id
    )
    raw_source = str(evidence_payload.get("source") or HOST_SOUL_API)
    source = raw_source.removesuffix(":reme")
    reme_source = f"{source}:reme"

    adapter = adapter_class(
        project_dir,
        workspace_dir=default_reme_workspace(project_dir),
    )
    write_mode = str(reme_config.get("write_mode") or REME_WRITE_MODE_AUTO_MEMORY)
    messages = normalized_reme_messages(
        evidence_payload=evidence_payload,
        episode_payload=episode_payload,
        task=task,
        outcome=outcome,
    )
    note_name = safe_reme_note_name(f"episode_{session_id}")
    actual_write_mode = write_mode
    if write_mode == REME_WRITE_MODE_FALLBACK_DAILY:
        content = render_reme_episode(task=task, outcome=outcome, episode=episode_payload)
        write_result = adapter.daily_write(
            name=note_name,
            description="Agent episode captured as ReMe memory for Soul evidence",
            session_id=safe_reme_session_id(session_id),
            content=content,
            date=str(reme_config.get("date") or ""),
            metadata={
                "source": source,
                "memory_owner": MEMORY_OWNER_REME,
                "state_owner": STATE_OWNER_SOUL,
                "memory_mode": MEMORY_MODE_SOUL_REME,
            },
        )
    elif write_mode == REME_WRITE_MODE_AUTO_MEMORY:
        try:
            write_result = adapter.auto_memory(
                session_id=safe_reme_session_id(session_id),
                messages=messages,
                memory_hint=str(
                    reme_config.get("memory_hint")
                    or "Preserve durable project decisions, constraints, procedures, user preferences, and evidence links."
                ),
                date=str(reme_config.get("date") or ""),
            )
        except RuntimeError as exc:
            if not should_fallback_reme_write(exc, reme_config):
                raise
            actual_write_mode = REME_WRITE_MODE_FALLBACK_DAILY
            content = render_reme_episode(task=task, outcome=outcome, episode=episode_payload)
            write_result = adapter.daily_write(
                name=note_name,
                description="Agent episode captured as ReMe memory for Soul evidence",
                session_id=safe_reme_session_id(session_id),
                content=content,
                date=str(reme_config.get("date") or ""),
                metadata={
                    "source": source,
                    "memory_owner": MEMORY_OWNER_REME,
                    "state_owner": STATE_OWNER_SOUL,
                    "memory_mode": MEMORY_MODE_SOUL_REME,
                    "fallback_reason": compact_transition_summary(str(exc)),
                },
            )
    else:
        raise ValueError(f"Unsupported ReMe write_mode: {write_mode}")
    search_query = " ".join(part for part in [task, outcome] if part).strip() or note_name
    search_result = adapter.search(query=search_query, limit=int_value(reme_config.get("search_limit"), default=5))
    refs = merge_evidence_refs(reme_evidence_refs(search_result.metadata), reme_write_refs(write_result.metadata))

    patch_evidence = {
        "source": reme_source,
        "task": task,
        "summary": compact_transition_summary(outcome),
        "content": "ReMe evidence refs attached; ordinary memory body remains in ReMe.",
        "memory_owner": MEMORY_OWNER_REME,
        "state_owner": STATE_OWNER_SOUL,
        "evidence_refs": refs,
        "reme": {
            "workspace_dir": str(adapter.workspace_dir),
            "write_mode": actual_write_mode,
            "requested_write_mode": write_mode,
            "write_paths": [ref["path"] for ref in reme_write_refs(write_result.metadata)],
            "search_counts": search_result.metadata.get("counts", {}),
        },
    }
    proposal = propose_patch(load_state(project_dir), patch_evidence, source=reme_source)
    append_patch_proposal(proposal, project_dir)
    trace_path = append_reme_state_trace(
        project_dir,
        task=task,
        write_metadata=write_result.metadata,
        search_metadata=search_result.metadata,
        evidence_refs=refs,
        proposal=proposal,
    )
    if record_integration_run is not None:
        record_integration_run(
            {
                "host": raw_source,
                "operation": OP_PROPOSE_REME_TRANSITION,
                "status": STATUS_SUCCESS,
                "memory_mode": MEMORY_MODE_SOUL_REME,
                "task": compact_transition_summary(task, 160),
                "patch_id": proposal.get("id"),
                "reme_write_mode": actual_write_mode,
                "reme_requested_write_mode": write_mode,
                "evidence_ref_count": len(refs),
                "trace_path": str(trace_path.relative_to(project_dir)).replace("\\", "/"),
            }
        )
    return {
        "memory_mode": MEMORY_MODE_SOUL_REME,
        "reme_write": write_result.metadata,
        "reme_write_mode": actual_write_mode,
        "reme_requested_write_mode": write_mode,
        "reme_search": search_result.metadata,
        "evidence_refs": refs,
        "patch_proposal": proposal,
        "trace_path": str(trace_path.relative_to(project_dir)).replace("\\", "/"),
    }


def render_reme_episode(task: str, outcome: str, episode: dict[str, Any]) -> str:
    events = episode.get("events", [])
    lines = [
        "# Soul Agent Episode",
        "",
        "## Task",
        task or "No task text was provided.",
        "",
        "## Outcome",
        outcome or "No outcome text was provided.",
        "",
        "## Event Summary",
        f"- event_count: {len(events) if isinstance(events, list) else 0}",
    ]
    return "\n".join(lines)


def normalized_reme_messages(
    *,
    evidence_payload: dict[str, Any],
    episode_payload: dict[str, Any],
    task: str,
    outcome: str,
) -> list[dict[str, Any]]:
    raw_messages = evidence_payload.get("messages") or episode_payload.get("messages")
    if isinstance(raw_messages, list) and raw_messages:
        messages: list[dict[str, Any]] = []
        for message in raw_messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            content = message.get("content", message.get("text", ""))
            normalized = {"name": role, "role": role, "content": str(content)}
            if message.get("created_at"):
                normalized["created_at"] = str(message["created_at"])
            messages.append(normalized)
        if messages:
            return messages
    return [
        {"name": "user", "role": "user", "content": task or "No task text was provided."},
        {"name": "assistant", "role": "assistant", "content": outcome or "No outcome text was provided."},
    ]


def should_fallback_reme_write(exc: RuntimeError, reme_config: dict[str, Any]) -> bool:
    if reme_config.get(REME_WRITE_MODE_FALLBACK_DAILY) is False:
        return False
    text = str(exc).lower()
    fallback_markers = [
        "missing credentials",
        "api_key",
        "openai_api_key",
        "workload_identity",
        "model",
        "exhausted all",
    ]
    return any(marker in text for marker in fallback_markers)
