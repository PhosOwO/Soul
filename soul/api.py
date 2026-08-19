from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from soul.adapters.reme import ReMeCliAdapter
from soul.services.shared.constants import (
    HOST_HTTP_API,
    HOST_SOUL_HTTP_API,
    MEMORY_MODE_SOUL_REME,
    OP_CONSOLIDATE_MEMORY,
    OP_GET_PROACTIVE_TOPICS,
    OP_GET_STATE,
    OP_READ_EVIDENCE,
    OP_TRACE_EVIDENCE,
    STATUS_SUCCESS,
)
from soul.services.reme.reme_refs import compact_transition_summary, resolve_reme_workspace
from soul.services.reme.reme_transition import propose_reme_transition as propose_reme_transition_service
from soul.services.integrations.integration_runs import append_integration_run
from soul.services.state_core.proposals import apply_patch_proposal, propose_patch
from soul.services.state_core.state_store import (
    append_patch_proposal,
    find_patch_proposal,
    load_state,
    load_state_markdown,
    save_state,
)
from soul.services.shared.state_types import PatchProposal


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class SoulApi:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def get_state(self, task: str = "", scope: str = "project", limit: int = 10, source: str = HOST_HTTP_API) -> dict[str, Any]:
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
                "operation": OP_GET_STATE,
                "status": STATUS_SUCCESS,
                "injected": bool(payload.get("injection")),
                "task": compact_transition_summary(task, 160),
                "state_version": (payload.get("state") or {}).get("version") if isinstance(payload.get("state"), dict) else None,
            }
        )
        return payload

    def propose_reme_transition(
        self,
        evidence: dict[str, Any] | None = None,
        episode: dict[str, Any] | None = None,
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return propose_reme_transition_service(
            project_dir=self.project_dir,
            evidence=evidence,
            episode=episode,
            reme=reme,
            record_integration_run=self.record_integration_run,
            adapter_class=ReMeCliAdapter,
        )

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
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "operation": OP_READ_EVIDENCE,
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
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "operation": OP_TRACE_EVIDENCE,
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
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "operation": OP_CONSOLIDATE_MEMORY,
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
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "operation": OP_GET_PROACTIVE_TOPICS,
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def propose_patch(self, evidence: dict[str, Any]) -> PatchProposal:
        proposal = propose_patch(load_state(self.project_dir), evidence, source=evidence.get("source", HOST_SOUL_HTTP_API))
        append_patch_proposal(proposal, self.project_dir)
        return proposal

    def apply_patch(self, proposal_id: str, confirmed_by: str = HOST_SOUL_HTTP_API) -> dict[str, Any]:
        proposal = find_patch_proposal(proposal_id, self.project_dir)
        state = load_state(self.project_dir)
        if proposal.get("base_version") != state.get("version"):
            raise ValueError(
                f"Patch base version {proposal.get('base_version')} does not match current state version {state.get('version')}."
            )
        next_state = apply_patch_proposal(state, proposal, confirmed_by=confirmed_by)
        save_state(next_state, self.project_dir)
        return {"state": next_state, "applied_patch": proposal}

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
                self.write_json({"ok": True, "service": HOST_SOUL_HTTP_API})
                return
            if parsed.path == "/state":
                query = parse_qs(parsed.query)
                task = first_query_value(query, "task", "")
                scope = first_query_value(query, "scope", "project")
                limit = int_value(first_query_value(query, "limit", "10"), default=10)
                source = first_query_value(query, "source", HOST_HTTP_API)
                self.write_json(api.get_state(task=task, scope=scope, limit=limit, source=source))
                return
            self.write_json({"error": "not_found"}, status=404)

        def do_POST(self) -> None:
            try:
                payload = self.read_json()
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
                    self.write_json(dict(api.propose_patch(payload.get("evidence", payload))))
                    return
                if self.path == "/patch/apply":
                    proposal_id = str(payload["proposal_id"])
                    confirmed_by = str(payload.get("confirmed_by", HOST_SOUL_HTTP_API))
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
