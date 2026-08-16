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
from soul.adapters.reme import ReMeCliAdapter, reme_evidence_refs
from soul.services.state import (
    apply_patch_proposal,
    append_patch_proposal,
    find_patch_proposal,
    load_state,
    propose_patch,
    record_state_event,
    save_state,
)
from soul.storage.database import connect, default_db_path, init_database


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class SoulApi:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def get_state(self, task: str = "", scope: str = "project", limit: int = 10) -> dict[str, Any]:
        with self.connect_existing() as conn:
            payload = SoulAgentAdapter(conn, self.project_dir).before_task(task, limit=limit)
        payload["scope"] = scope
        payload["injection"] = build_agent_injection(payload["context"])
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

        with self.connect_existing() as conn:
            result = SoulAgentAdapter(conn, self.project_dir).after_task(
                task=task or "DeepSeek Harness turn",
                outcome=outcome or "No outcome text was provided.",
                evidence={
                    **evidence_payload,
                    "source": evidence_payload.get("source", "deepseek-harness"),
                    "episode": episode_payload,
                },
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
        fallback_session_id = "deepseek-harness-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        session_id = str(
            evidence_payload.get("session_id")
            or episode_payload.get("session_id")
            or fallback_session_id
        )

        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, reme_config.get("workspace_dir")),
        )
        note_name = safe_reme_note_name(f"dsh_{session_id}")
        content = render_reme_episode(task=task, outcome=outcome, episode=episode_payload)
        write_result = adapter.daily_write(
            name=note_name,
            description="DeepSeek Harness episode captured as ReMe memory",
            session_id=safe_reme_session_id(session_id),
            content=content,
            date=str(reme_config.get("date") or ""),
            metadata={
                "source": "deepseek-harness",
                "memory_owner": "reme",
                "state_owner": "soul",
                "memory_mode": "soul_reme",
            },
        )
        search_query = " ".join(part for part in [task, outcome] if part).strip() or note_name
        search_result = adapter.search(query=search_query, limit=int(reme_config.get("search_limit") or 5))
        refs = reme_evidence_refs(search_result.metadata)
        if not refs and write_result.metadata.get("path"):
            refs = [{"type": "reme_file", "path": str(write_result.metadata["path"]).replace("\\", "/")}]

        patch_evidence = {
            "source": "deepseek-harness:reme",
            "task": task,
            "summary": compact_transition_summary(outcome),
            "content": "ReMe evidence refs attached; ordinary memory body remains in ReMe.",
            "memory_owner": "reme",
            "state_owner": "soul",
            "evidence_refs": refs,
            "reme": {
                "workspace_dir": str(adapter.workspace_dir),
                "daily_path": write_result.metadata.get("path"),
                "search_counts": search_result.metadata.get("counts", {}),
            },
        }
        proposal = propose_patch(load_state(self.project_dir), patch_evidence, source="deepseek-harness:reme")
        append_patch_proposal(proposal, self.project_dir)
        trace_path = append_reme_state_trace(
            self.project_dir,
            task=task,
            write_metadata=write_result.metadata,
            search_metadata=search_result.metadata,
            evidence_refs=refs,
            proposal=proposal,
        )
        return {
            "memory_mode": "soul_reme",
            "reme_daily_write": write_result.metadata,
            "reme_search": search_result.metadata,
            "evidence_refs": refs,
            "patch_proposal": proposal,
            "trace_path": str(trace_path.relative_to(self.project_dir)).replace("\\", "/"),
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
        with self.connect_existing() as conn:
            record_state_event(
                conn,
                "state_patch_applied",
                proposal,
                "soul-http-api",
                f"Applied State Patch {proposal_id}",
            )
            conn.commit()
        return {"state": next_state, "applied_patch": proposal}

    def connect_existing(self):
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
                limit = int(first_query_value(query, "limit", "10"))
                self.write_json(api.get_state(task=task, scope=scope, limit=limit))
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


def resolve_reme_workspace(project_dir: Path, raw_workspace: Any) -> Path:
    if not raw_workspace:
        return project_dir / ".soul" / "reme"
    path = Path(str(raw_workspace))
    return path if path.is_absolute() else project_dir / path


def render_reme_episode(task: str, outcome: str, episode: dict[str, Any]) -> str:
    events = episode.get("events", [])
    lines = [
        "# DeepSeek Harness Episode",
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


def safe_reme_note_name(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", raw.strip())[:80].strip("_")
    return name or "deepseek_harness_episode"


def safe_reme_session_id(raw: str) -> str:
    session_id = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw.strip())[:120].strip("-")
    return session_id or "deepseek-harness"


def compact_transition_summary(text: str, limit: int = 240) -> str:
    summary = " ".join(text.split())
    if not summary:
        return "DeepSeek Harness episode was written to ReMe; review evidence refs for state changes."
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
        f"- reme_daily_path: {write_metadata.get('path', '')}",
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
