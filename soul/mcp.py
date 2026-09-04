from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from soul import __version__
from soul.api import SoulApi
from soul.services.project_resolver import find_git_root, find_soul_project_root, resolve_project_dir
from soul.services.shared.constants import HOST_SOUL_MCP
from soul.services.state_core.state_store import load_state


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
                "project_dir": {"type": "string"},
                "cwd": {"type": "string"},
            },
        },
    },
    {
        "name": "observe_evidence",
        "description": (
            "Enqueue turn evidence for non-blocking ReMe processing. "
            "Background processing may later update unconfirmed Working State."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "outcome": {"type": "string"},
                "summary": {"type": "string"},
                "source": {"type": "string"},
                "events": {"type": "array", "items": {"type": "object"}},
                "messages": {"type": "array", "items": {"type": "object"}},
                "episode": {"type": "object"},
                "session_id": {"type": "string"},
                "turn_id": {"type": "string"},
                "project_dir": {"type": "string"},
                "cwd": {"type": "string"},
                "reme": {
                    "type": "object",
                    "properties": {
                        "search_limit": {"type": "integer", "minimum": 1},
                        "date": {"type": "string"},
                        "memory_hint": {"type": "string"},
                    },
                },
            },
        },
    },
    {
        "name": "read_evidence",
        "description": "Read a ReMe evidence file or line range by workspace-relative path. Does not modify Soul state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "project_dir": {"type": "string"},
                "cwd": {"type": "string"},
                "reme": {"type": "object", "properties": {"workspace_dir": {"type": "string"}}},
            },
            "required": ["path"],
        },
    },
    {
        "name": "trace_evidence",
        "description": "Traverse ReMe wikilink neighbors for an evidence path. Does not modify Soul state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "depth": {"type": "integer", "minimum": 1},
                "direction": {"type": "string", "enum": ["both", "in", "out"]},
                "project_dir": {"type": "string"},
                "cwd": {"type": "string"},
                "reme": {"type": "object", "properties": {"workspace_dir": {"type": "string"}}},
            },
            "required": ["path"],
        },
    },
    {
        "name": "consolidate_memory",
        "description": "Run ReMe auto_dream explicitly to consolidate daily memory into digest. Does not modify Soul state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "hint": {"type": "string"},
                "scan_days": {"type": "integer", "minimum": 1},
                "max_units": {"type": "integer", "minimum": 1},
                "project_dir": {"type": "string"},
                "cwd": {"type": "string"},
                "reme": {"type": "object", "properties": {"workspace_dir": {"type": "string"}}},
            },
        },
    },
    {
        "name": "get_proactive_topics",
        "description": "Read ReMe proactive topics from interests.yaml. Missing topics are an empty state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "include_content": {"type": "boolean"},
                "project_dir": {"type": "string"},
                "cwd": {"type": "string"},
                "reme": {"type": "object", "properties": {"workspace_dir": {"type": "string"}}},
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
                "project_dir": {"type": "string"},
                "cwd": {"type": "string"},
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
                "project_dir": {"type": "string"},
                "cwd": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
]


class SoulMcpServer:
    def __init__(self, project_dir: Path | None = None) -> None:
        self.project_dir = resolve_project_dir(project_dir)

    def existing_project_api_for_arguments(self, arguments: dict[str, Any]) -> SoulApi | None:
        project_dir = find_soul_project_root(self.start_path_for_arguments(arguments))
        if project_dir is None:
            return None
        return SoulApi(project_dir, register=False)

    def activated_project_api_for_arguments(self, arguments: dict[str, Any]) -> SoulApi | None:
        start = self.start_path_for_arguments(arguments)
        project_dir = find_soul_project_root(start) or find_git_root(start)
        if project_dir is None:
            return None
        load_state(project_dir, project_name=project_dir.name)
        return SoulApi(project_dir, register=True)

    def start_path_for_arguments(self, arguments: dict[str, Any]) -> Path:
        raw_project_dir = arguments.get("project_dir")
        raw_cwd = arguments.get("cwd")
        return Path(str(raw_project_dir or raw_cwd or self.project_dir)).expanduser().resolve()

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        raw_params = request.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}

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
            api = self.existing_project_api_for_arguments(arguments)
            if api is None:
                return skipped_tool_result(
                    "get_projected_state",
                    "soul_project_not_found",
                    project_dir=self.start_path_for_arguments(arguments),
                )
            payload = api.get_state(
                task=str(arguments.get("task", "")),
                scope=str(arguments.get("scope", "project")),
                limit=int_value(arguments.get("limit"), default=10),
                source=str(arguments.get("source", "codex:mcp")),
            )
            return tool_result(payload)

        if name == "observe_evidence":
            evidence = {
                "task": arguments.get("task", ""),
                "outcome": arguments.get("outcome") or arguments.get("summary") or "",
                "summary": arguments.get("summary") or arguments.get("outcome") or "",
                "source": arguments.get("source", "codex:mcp"),
                "events": arguments.get("events", []),
            }
            if arguments.get("session_id"):
                evidence["session_id"] = arguments.get("session_id")
            if arguments.get("turn_id"):
                evidence["turn_id"] = arguments.get("turn_id")
            if isinstance(arguments.get("messages"), list):
                evidence["messages"] = arguments.get("messages")
            episode = arguments.get("episode")
            episode_payload = episode if isinstance(episode, dict) else None
            reme = arguments.get("reme")
            api = self.activated_project_api_for_arguments(arguments)
            if api is None:
                return skipped_tool_result(
                    "observe_evidence",
                    "not_soul_project_or_git_repo",
                    project_dir=self.start_path_for_arguments(arguments),
                )
            payload = api.enqueue_evidence(
                evidence=evidence,
                episode=episode_payload,
                reme=reme if isinstance(reme, dict) else None,
            )
            return tool_result(payload)

        if name == "read_evidence":
            reme = arguments.get("reme")
            api = self.existing_project_api_for_arguments(arguments)
            if api is None:
                return skipped_tool_result(
                    "read_evidence",
                    "soul_project_not_found",
                    project_dir=self.start_path_for_arguments(arguments),
                )
            return tool_result(
                api.read_evidence(
                    path=str(arguments["path"]),
                    start_line=optional_int(arguments.get("start_line")),
                    end_line=optional_int(arguments.get("end_line")),
                    reme=reme if isinstance(reme, dict) else None,
                )
            )

        if name == "trace_evidence":
            reme = arguments.get("reme")
            api = self.existing_project_api_for_arguments(arguments)
            if api is None:
                return skipped_tool_result(
                    "trace_evidence",
                    "soul_project_not_found",
                    project_dir=self.start_path_for_arguments(arguments),
                )
            return tool_result(
                api.trace_evidence(
                    path=str(arguments["path"]),
                    depth=int_value(arguments.get("depth"), default=1),
                    direction=str(arguments.get("direction") or "both"),
                    reme=reme if isinstance(reme, dict) else None,
                )
            )

        if name == "consolidate_memory":
            reme = arguments.get("reme")
            api = self.existing_project_api_for_arguments(arguments)
            if api is None:
                return skipped_tool_result(
                    "consolidate_memory",
                    "soul_project_not_found",
                    project_dir=self.start_path_for_arguments(arguments),
                )
            return tool_result(
                api.consolidate_memory(
                    date=str(arguments.get("date") or ""),
                    hint=str(arguments.get("hint") or ""),
                    scan_days=optional_int(arguments.get("scan_days")),
                    max_units=optional_int(arguments.get("max_units")),
                    reme=reme if isinstance(reme, dict) else None,
                )
            )

        if name == "get_proactive_topics":
            reme = arguments.get("reme")
            api = self.existing_project_api_for_arguments(arguments)
            if api is None:
                return skipped_tool_result(
                    "get_proactive_topics",
                    "soul_project_not_found",
                    project_dir=self.start_path_for_arguments(arguments),
                )
            return tool_result(
                api.get_proactive_topics(
                    date=str(arguments.get("date") or ""),
                    include_content=bool(arguments.get("include_content", False)),
                    reme=reme if isinstance(reme, dict) else None,
                )
            )

        if name == "propose_patch":
            evidence = arguments.get("evidence")
            if not isinstance(evidence, dict):
                raise ValueError("propose_patch requires object argument: evidence")
            api = self.activated_project_api_for_arguments(arguments)
            if api is None:
                return skipped_tool_result(
                    "propose_patch",
                    "not_soul_project_or_git_repo",
                    project_dir=self.start_path_for_arguments(arguments),
                )
            return tool_result(dict(api.propose_patch(evidence)))

        if name == "apply_patch":
            proposal_id = str(arguments["proposal_id"])
            confirmed_by = str(arguments.get("confirmed_by", HOST_SOUL_MCP))
            api = self.activated_project_api_for_arguments(arguments)
            if api is None:
                return skipped_tool_result(
                    "apply_patch",
                    "not_soul_project_or_git_repo",
                    project_dir=self.start_path_for_arguments(arguments),
                )
            return tool_result(api.apply_patch(proposal_id, confirmed_by=confirmed_by))

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


def skipped_payload(operation: str, reason: str, *, project_dir: Path) -> dict[str, Any]:
    return {
        "operation": operation,
        "skipped": True,
        "reason": reason,
        "project_dir": str(project_dir),
        "context": "",
        "injection": "",
    }


def skipped_tool_result(operation: str, reason: str, *, project_dir: Path) -> dict[str, Any]:
    return tool_result(skipped_payload(operation, reason, project_dir=project_dir))


def int_value(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--project-dir", default=".", help="Soul project directory containing .soul/")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serve_stdio(Path(args.project_dir))


if __name__ == "__main__":
    main()
