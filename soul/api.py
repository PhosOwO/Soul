from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from soul.adapters.agent import SoulAgentAdapter
from soul.adapters.reme import ReMeCliAdapter, reme_evidence_refs, reme_write_refs
from soul.services.state import (
    apply_patch_proposal,
    append_patch_proposal,
    find_patch_proposal,
    load_state_markdown,
    load_state,
    propose_patch,
    record_state_event,
    save_state,
)
from soul.services.integration_runs import append_integration_run
from soul.storage.database import connect, default_db_path, init_database


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class SoulApi:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def get_state(self, task: str = "", scope: str = "project", limit: int = 10, source: str = "http-api") -> dict[str, Any]:
        state = load_state(self.project_dir, project_name=self.project_dir.name)
        payload = {
            "task": task,
            "state": state,
            "context": load_state_markdown(self.project_dir, limit=limit, task=task),
            "state_artifact": ".soul/state/STATE.md",
        }
        payload["scope"] = scope
        payload["injection"] = build_agent_injection(payload["context"])
        self.record_integration_run(
            {
                "host": source,
                "operation": "get_state",
                "status": "success",
                "injected": bool(payload.get("injection")),
                "task": compact_transition_summary(task, 160),
                "state_version": (payload.get("state") or {}).get("version") if isinstance(payload.get("state"), dict) else None,
            }
        )
        return payload

    def propose_transition(
        self,
        evidence: dict[str, Any] | None = None,
        episode: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evidence_payload = dict(evidence or {})
        episode_payload = episode or {}
        task = str(evidence_payload.get("task") or episode_payload.get("task") or "")
        outcome = str(
            evidence_payload.get("outcome")
            or evidence_payload.get("summary")
            or episode_payload.get("outcome")
            or ""
        )

        with self.connect_legacy_database() as conn:
            result = SoulAgentAdapter(conn, self.project_dir).after_task(
                task=task or "DeepSeek Harness turn",
                outcome=outcome or "No outcome text was provided.",
                evidence={
                    **evidence_payload,
                    "source": evidence_payload.get("source", "deepseek-harness"),
                    "episode": episode_payload,
                },
            )
        self.record_integration_run(
            {
                "host": str(evidence_payload.get("source") or "deepseek-harness"),
                "operation": "propose_transition",
                "status": "success",
                "memory_mode": "legacy",
                "task": compact_transition_summary(task, 160),
                "episode_id": result.get("episode_id"),
                "patch_id": (result.get("patch_proposal") or {}).get("id")
                if isinstance(result.get("patch_proposal"), dict)
                else None,
            }
        )
        return result

    def propose_reme_transition(
        self,
        evidence: dict[str, Any] | None = None,
        episode: dict[str, Any] | None = None,
        reme: dict[str, Any] | None = None,
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
        raw_source = str(evidence_payload.get("source") or "soul-api")
        source = raw_source.removesuffix(":reme")
        reme_source = f"{source}:reme"

        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=default_reme_workspace(self.project_dir),
        )
        write_mode = str(reme_config.get("write_mode") or "auto_memory")
        messages = normalized_reme_messages(
            evidence_payload=evidence_payload,
            episode_payload=episode_payload,
            task=task,
            outcome=outcome,
        )
        note_name = safe_reme_note_name(f"episode_{session_id}")
        actual_write_mode = write_mode
        if write_mode == "fallback_daily_write":
            content = render_reme_episode(task=task, outcome=outcome, episode=episode_payload)
            write_result = adapter.daily_write(
                name=note_name,
                description="Agent episode captured as ReMe memory for Soul evidence",
                session_id=safe_reme_session_id(session_id),
                content=content,
                date=str(reme_config.get("date") or ""),
                metadata={
                    "source": source,
                    "memory_owner": "reme",
                    "state_owner": "soul",
                    "memory_mode": "soul_reme",
                },
            )
        elif write_mode == "auto_memory":
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
                actual_write_mode = "fallback_daily_write"
                content = render_reme_episode(task=task, outcome=outcome, episode=episode_payload)
                write_result = adapter.daily_write(
                    name=note_name,
                    description="Agent episode captured as ReMe memory for Soul evidence",
                    session_id=safe_reme_session_id(session_id),
                    content=content,
                    date=str(reme_config.get("date") or ""),
                    metadata={
                        "source": source,
                        "memory_owner": "reme",
                        "state_owner": "soul",
                        "memory_mode": "soul_reme",
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
            "memory_owner": "reme",
            "state_owner": "soul",
            "evidence_refs": refs,
            "reme": {
                "workspace_dir": str(adapter.workspace_dir),
                "write_mode": actual_write_mode,
                "requested_write_mode": write_mode,
                "write_paths": [ref["path"] for ref in reme_write_refs(write_result.metadata)],
                "search_counts": search_result.metadata.get("counts", {}),
            },
        }
        proposal = propose_patch(load_state(self.project_dir), patch_evidence, source=reme_source)
        append_patch_proposal(proposal, self.project_dir)
        trace_path = append_reme_state_trace(
            self.project_dir,
            task=task,
            write_metadata=write_result.metadata,
            search_metadata=search_result.metadata,
            evidence_refs=refs,
            proposal=proposal,
        )
        self.record_integration_run(
            {
                "host": raw_source,
                "operation": "propose_reme_transition",
                "status": "success",
                "memory_mode": "soul_reme",
                "task": compact_transition_summary(task, 160),
                "patch_id": proposal.get("id"),
                "reme_write_mode": actual_write_mode,
                "reme_requested_write_mode": write_mode,
                "evidence_ref_count": len(refs),
                "trace_path": str(trace_path.relative_to(self.project_dir)).replace("\\", "/"),
            }
        )
        return {
            "memory_mode": "soul_reme",
            "reme_write": write_result.metadata,
            "reme_write_mode": actual_write_mode,
            "reme_requested_write_mode": write_mode,
            "reme_search": search_result.metadata,
            "evidence_refs": refs,
            "patch_proposal": proposal,
            "trace_path": str(trace_path.relative_to(self.project_dir)).replace("\\", "/"),
        }

    def record_integration_run(self, record: dict[str, Any]) -> None:
        try:
            append_integration_run(self.project_dir, record)
        except OSError:
            return

    def read_evidence(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, (reme or {}).get("workspace_dir")),
        )
        result = adapter.read(path=path, start_line=start_line, end_line=end_line)
        return {
            "memory_mode": "soul_reme",
            "operation": "read_evidence",
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def trace_evidence(
        self,
        path: str,
        depth: int = 1,
        direction: str = "both",
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, (reme or {}).get("workspace_dir")),
        )
        result = adapter.traverse(path=path, depth=depth, direction=direction)
        return {
            "memory_mode": "soul_reme",
            "operation": "trace_evidence",
            "path": path,
            "depth": depth,
            "direction": direction,
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def consolidate_memory(
        self,
        date: str = "",
        hint: str = "",
        scan_days: int | None = None,
        max_units: int | None = None,
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, (reme or {}).get("workspace_dir")),
        )
        result = adapter.auto_dream(date=date, hint=hint, scan_days=scan_days, max_units=max_units)
        return {
            "memory_mode": "soul_reme",
            "operation": "consolidate_memory",
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def get_proactive_topics(
        self,
        date: str = "",
        include_content: bool = False,
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, (reme or {}).get("workspace_dir")),
        )
        result = adapter.proactive(date=date, include_content=include_content)
        return {
            "memory_mode": "soul_reme",
            "operation": "get_proactive_topics",
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def propose_patch(self, evidence: dict[str, Any]) -> dict[str, Any]:
        proposal = propose_patch(load_state(self.project_dir), evidence, source=evidence.get("source", "soul-http-api"))
        append_patch_proposal(proposal, self.project_dir)
        return proposal

    def apply_patch(self, proposal_id: str, confirmed_by: str = "soul-http-api") -> dict[str, Any]:
        proposal = find_patch_proposal(proposal_id, self.project_dir)
        state = load_state(self.project_dir)
        if proposal.get("base_version") != state.get("version"):
            raise ValueError(
                f"Patch base version {proposal.get('base_version')} does not match current state version {state.get('version')}."
            )
        next_state = apply_patch_proposal(state, proposal, confirmed_by=confirmed_by)
        save_state(next_state, self.project_dir)
        return {"state": next_state, "applied_patch": proposal}

    def connect_legacy_database(self):
        db_path = default_db_path(self.project_dir)
        conn = connect(db_path)
        init_database(conn, project_name=self.project_dir.name)
        return conn


def build_agent_injection(context: str) -> str:
    return (
        "[Soul Current State]\n"
        "Use this as accepted project cognition for continuity and constraints. "
        "Do not treat Soul as a planner or executor; only use the state to improve the next answer.\n\n"
        f"{context}\n"
        "[/Soul Current State]"
    )


def make_handler(api: SoulApi) -> type[BaseHTTPRequestHandler]:
    class SoulApiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self.write_json({"ok": True, "service": "soul-http-api"})
                return
            if parsed.path == "/state":
                query = parse_qs(parsed.query)
                task = first_query_value(query, "task", "")
                scope = first_query_value(query, "scope", "project")
                limit = int_value(first_query_value(query, "limit", "10"), default=10)
                source = first_query_value(query, "source", "http-api")
                self.write_json(api.get_state(task=task, scope=scope, limit=limit, source=source))
                return
            self.write_json({"error": "not_found"}, status=404)

        def do_POST(self) -> None:
            try:
                payload = self.read_json()
                if self.path == "/transition/propose":
                    self.write_json(
                        api.propose_transition(
                            evidence=payload.get("evidence"),
                            episode=payload.get("episode"),
                        )
                    )
                    return
                if self.path == "/reme/transition/propose":
                    self.write_json(
                        api.propose_reme_transition(
                            evidence=payload.get("evidence"),
                            episode=payload.get("episode"),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/reme/evidence/read":
                    self.write_json(
                        api.read_evidence(
                            path=str(payload["path"]),
                            start_line=optional_int(payload.get("start_line")),
                            end_line=optional_int(payload.get("end_line")),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/reme/evidence/trace":
                    self.write_json(
                        api.trace_evidence(
                            path=str(payload["path"]),
                            depth=int_value(payload.get("depth"), default=1),
                            direction=str(payload.get("direction") or "both"),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/reme/memory/consolidate":
                    self.write_json(
                        api.consolidate_memory(
                            date=str(payload.get("date") or ""),
                            hint=str(payload.get("hint") or ""),
                            scan_days=optional_int(payload.get("scan_days")),
                            max_units=optional_int(payload.get("max_units")),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/reme/proactive":
                    self.write_json(
                        api.get_proactive_topics(
                            date=str(payload.get("date") or ""),
                            include_content=bool(payload.get("include_content", False)),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/patch/propose":
                    self.write_json(api.propose_patch(payload.get("evidence", payload)))
                    return
                if self.path == "/patch/apply":
                    proposal_id = str(payload["proposal_id"])
                    confirmed_by = str(payload.get("confirmed_by", "soul-http-api"))
                    self.write_json(api.apply_patch(proposal_id, confirmed_by=confirmed_by))
                    return
                self.write_json({"error": "not_found"}, status=404)
            except Exception as exc:
                self.write_json({"error": str(exc)}, status=400)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def write_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SoulApiHandler


def first_query_value(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default


def int_value(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def resolve_reme_workspace(project_dir: Path, raw_workspace: Any) -> Path:
    if not raw_workspace:
        return default_reme_workspace(project_dir)
    path = Path(str(raw_workspace))
    return path if path.is_absolute() else project_dir / path


def default_reme_workspace(project_dir: Path) -> Path:
    return project_dir / ".soul" / "reme"


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
    if reme_config.get("fallback_daily_write") is False:
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


def merge_evidence_refs(*ref_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for refs in ref_lists:
        for ref in refs:
            key = (ref.get("type"), ref.get("path"), ref.get("start_line"), ref.get("end_line"), ref.get("chunk_id"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
    return merged


def safe_reme_note_name(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", raw.strip())[:80].strip("_")
    return name or "deepseek_harness_episode"


def safe_reme_session_id(raw: str) -> str:
    session_id = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw.strip())[:120].strip("-")
    return session_id or "soul-agent"


def compact_transition_summary(text: str, limit: int = 240) -> str:
    summary = " ".join(text.split())
    if not summary:
        return "Agent episode was written to ReMe; review evidence refs for state changes."
    if len(summary) <= limit:
        return summary
    return summary[: limit - 3].rstrip() + "..."


def append_reme_state_trace(
    project_dir: Path,
    *,
    task: str,
    write_metadata: dict[str, Any],
    search_metadata: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
    proposal: dict[str, Any],
) -> Path:
    trace_path = project_dir / ".soul" / "traces" / "reme_state_trace.md"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    counts = search_metadata.get("counts", {})
    lines = [
        f"## {datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
        "",
        f"- task: {compact_transition_summary(task, 160)}",
        f"- reme_write_paths: {', '.join(ref['path'] for ref in reme_write_refs(write_metadata))}",
        f"- search_counts: vector={counts.get('vector', 0)}, keyword={counts.get('keyword', 0)}, returned={counts.get('returned', 0)}, hybrid={counts.get('hybrid', False)}",
        f"- soul_patch_id: {proposal.get('id', '')}",
        f"- soul_patch_status: {proposal.get('status', '')}",
        f"- review_recommendation: {proposal.get('review_recommendation', '')}",
        "- evidence_refs:",
    ]
    if evidence_refs:
        for ref in evidence_refs:
            line_range = ""
            if ref.get("start_line") is not None and ref.get("end_line") is not None:
                line_range = f":{ref['start_line']}-{ref['end_line']}"
            chunk = f"#{ref['chunk_id']}" if ref.get("chunk_id") else ""
            lines.append(f"  - reme://{ref.get('path', '')}{line_range}{chunk}")
    else:
        lines.append("  - none")
    lines.append("")
    with trace_path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))
    return trace_path


def serve(project_dir: Path, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    api = SoulApi(project_dir.resolve())
    server = ThreadingHTTPServer((host, port), make_handler(api))
    print(f"Soul HTTP API listening on http://{host}:{port} for {project_dir.resolve()}")
    server.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the minimal local Soul HTTP API.")
    parser.add_argument("--project-dir", default=".", help="Soul project directory containing .soul/")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serve(Path(args.project_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
