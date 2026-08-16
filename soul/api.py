from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from soul.adapters.agent import SoulAgentAdapter
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


def serve(project_dir: Path, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    api = SoulApi(project_dir.resolve())
    server = ThreadingHTTPServer((host, port), make_handler(api))
    print(f"Soul HTTP API listening on http://{host}:{port} for {project_dir.resolve()}")
    server.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the minimal local Soul HTTP API.")
    parser.add_argument("--project-dir", default=".", help="Soul project directory containing .brain/")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serve(Path(args.project_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
