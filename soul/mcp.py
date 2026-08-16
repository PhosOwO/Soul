from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from soul.api import SoulApi


SERVER_NAME = "soul-core-mcp"
SERVER_VERSION = "0.1.0"


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_projected_state",
        "description": "Get Soul Current State projected for a task. Use as context only, not as a planner.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "scope": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
        },
    },
    {
        "name": "observe_evidence",
        "description": "Submit turn evidence and receive a State Patch proposal. The patch is not applied automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "outcome": {"type": "string"},
                "summary": {"type": "string"},
                "source": {"type": "string"},
                "events": {"type": "array", "items": {"type": "object"}},
                "episode": {"type": "object"},
            },
        },
    },
    {
        "name": "propose_patch",
        "description": "Propose a State Patch from explicit evidence. The patch is not applied automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "evidence": {"type": "object"},
            },
            "required": ["evidence"],
        },
    },
    {
        "name": "apply_patch",
        "description": "Apply a previously proposed State Patch by id after explicit review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "confirmed_by": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
]


class SoulMcpServer:
    def __init__(self, project_dir: Path) -> None:
        self.api = SoulApi(project_dir)

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}

        try:
            if method == "notifications/initialized":
                return None
            if method == "initialize":
                return self.result(
                    request_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    },
                )
            if method == "tools/list":
                return self.result(request_id, {"tools": TOOLS})
            if method == "tools/call":
                name = str(params.get("name", ""))
                arguments = params.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                return self.result(request_id, self.call_tool(name, arguments))
            return self.error(request_id, -32601, f"Unknown method: {method}")
        except Exception as exc:
            return self.error(request_id, -32000, str(exc))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_projected_state":
            payload = self.api.get_state(
                task=str(arguments.get("task", "")),
                scope=str(arguments.get("scope", "project")),
                limit=int(arguments.get("limit", 10)),
            )
            return tool_result(payload)

        if name == "observe_evidence":
            evidence = {
                "task": arguments.get("task", ""),
                "outcome": arguments.get("outcome") or arguments.get("summary") or "",
                "summary": arguments.get("summary") or arguments.get("outcome") or "",
                "source": arguments.get("source", "soul-mcp"),
                "events": arguments.get("events", []),
            }
            episode = arguments.get("episode")
            payload = self.api.propose_transition(
                evidence=evidence,
                episode=episode if isinstance(episode, dict) else None,
            )
            return tool_result(payload)

        if name == "propose_patch":
            evidence = arguments.get("evidence")
            if not isinstance(evidence, dict):
                raise ValueError("propose_patch requires object argument: evidence")
            return tool_result(self.api.propose_patch(evidence))

        if name == "apply_patch":
            proposal_id = str(arguments["proposal_id"])
            confirmed_by = str(arguments.get("confirmed_by", "soul-mcp"))
            return tool_result(self.api.apply_patch(proposal_id, confirmed_by=confirmed_by))

        raise ValueError(f"Unknown tool: {name}")

    @staticmethod
    def result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": payload}

    @staticmethod
    def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ],
        "structuredContent": payload,
    }


def serve_stdio(project_dir: Path) -> None:
    server = SoulMcpServer(project_dir.resolve())
    for line in sys.stdin:
        if not line.strip():
            continue
        response = server.handle(json.loads(line))
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the minimal Soul MCP stdio server.")
    parser.add_argument("--project-dir", default=".", help="Soul project directory containing .brain/")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serve_stdio(Path(args.project_dir))


if __name__ == "__main__":
    main()
