from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from soul.adapters.reme import ReMeJobResult
from soul.mcp import SoulMcpServer
from soul.services.state import load_state


def require_response(response: dict[str, Any] | None) -> dict[str, Any]:
    assert response is not None
    return response


def test_mcp_lists_soul_tools(tmp_path: Path) -> None:
    load_state(tmp_path, project_name="Demo")

    response = require_response(SoulMcpServer(tmp_path).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))

    tools = response["result"]["tools"]
    assert {tool["name"] for tool in tools} >= {
        "get_projected_state",
        "observe_evidence",
        "read_evidence",
        "trace_evidence",
        "consolidate_memory",
        "get_proactive_topics",
        "propose_patch",
        "apply_patch",
    }
    observe_tool = next(tool for tool in tools if tool["name"] == "observe_evidence")
    assert "memory_mode" not in observe_tool["inputSchema"]["properties"]


def test_mcp_get_projected_state_returns_tool_content(tmp_path: Path) -> None:
    load_state(tmp_path, project_name="Demo")

    response = require_response(
        SoulMcpServer(tmp_path).handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "get_projected_state",
                    "arguments": {"task": "What should we do next?", "limit": 4},
                },
            }
        )
    )

    result = response["result"]
    payload = json.loads(result["content"][0]["text"])
    assert "Soul Current State" in payload["injection"]
    assert result["structuredContent"]["task"] == "What should we do next?"
    runs = (tmp_path / ".soul" / "state" / "integration_runs.jsonl").read_text(encoding="utf-8")
    assert '"host": "codex:mcp"' in runs
    assert '"operation": "get_state"' in runs


def test_mcp_observe_evidence_writes_reme_and_proposes_refs_only_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_state(tmp_path, project_name="Demo")

    class FakeReMeAdapter:
        def __init__(self, project_dir: Path, workspace_dir: Path | None = None) -> None:
            self.workspace_dir = workspace_dir

        def auto_memory(self, **kwargs: Any) -> ReMeJobResult:
            return ReMeJobResult(
                job="auto_memory",
                command=["reme", "auto_memory"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={"path": "daily/2026-08-17/codex_session.md"},
            )

        def search(self, **kwargs: Any) -> ReMeJobResult:
            return ReMeJobResult(
                job="search",
                command=["reme", "start", "job=search"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={
                    "counts": {"vector": 0, "keyword": 1, "returned": 1},
                    "results": [
                        {
                            "id": "chunk-1",
                            "path": "daily/2026-08-17/codex_session.md",
                            "start_line": 3,
                            "end_line": 9,
                            "score": 1.5,
                        }
                    ],
                },
            )

    monkeypatch.setattr("soul.api.ReMeCliAdapter", FakeReMeAdapter)

    response = require_response(
        SoulMcpServer(tmp_path).handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "observe_evidence",
                    "arguments": {
                        "task": "Review evidence",
                        "summary": "A new observation should be reviewed before becoming accepted state.",
                        "source": "test-mcp",
                        "messages": [
                            {"role": "user", "content": "Review evidence"},
                            {
                                "role": "assistant",
                                "content": "A new observation should be reviewed before becoming accepted state.",
                            },
                        ],
                    },
                },
            }
        )
    )

    payload = response["result"]["structuredContent"]
    assert payload["memory_mode"] == "soul_reme"
    assert payload["reme_write_mode"] == "auto_memory"
    assert payload["evidence_refs"][0]["path"] == "daily/2026-08-17/codex_session.md"
    assert payload["patch_proposal"]["status"] == "proposed"
    assert payload["patch_proposal"]["review_recommendation"] == "reject"
    assert payload["patch_proposal"]["knowledge_points"] == []
    assert payload["patch_proposal"]["evidence"]["source"] == "test-mcp:reme"
    assert payload["patch_proposal"]["evidence"]["memory_owner"] == "reme"
    assert payload["patch_proposal"]["evidence"]["content"] == "ReMe evidence refs attached; ordinary memory body remains in ReMe."
    runs = (tmp_path / ".soul" / "state" / "integration_runs.jsonl").read_text(encoding="utf-8")
    assert '"host": "test-mcp"' in runs
    assert '"operation": "propose_reme_transition"' in runs


def test_mcp_reme_evidence_and_memory_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    load_state(tmp_path, project_name="Demo")

    class FakeReMeAdapter:
        def __init__(self, project_dir: Path, workspace_dir: Path | None = None) -> None:
            self.workspace_dir = workspace_dir

        def read(self, **kwargs: Any) -> ReMeJobResult:
            return ReMeJobResult("read", ["reme", "read"], 0, "", "", "read answer", {"path": kwargs["path"]})

        def traverse(self, **kwargs: Any) -> ReMeJobResult:
            return ReMeJobResult("traverse", ["reme", "traverse"], 0, "", "", "trace answer", {"path": kwargs["path"]})

        def auto_dream(self, **kwargs: Any) -> ReMeJobResult:
            return ReMeJobResult("auto_dream", ["reme", "auto_dream"], 0, "", "", "dream answer", {"integrated": 1})

        def proactive(self, **kwargs: Any) -> ReMeJobResult:
            return ReMeJobResult("proactive", ["reme", "proactive"], 0, "", "", "topic answer", {"topics": []})

    monkeypatch.setattr("soul.api.ReMeCliAdapter", FakeReMeAdapter)
    server = SoulMcpServer(tmp_path)

    read_response = require_response(
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "read_evidence",
                    "arguments": {"path": "daily/a.md", "start_line": 1, "end_line": 2},
                },
            }
        )
    )
    trace_response = require_response(
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "trace_evidence",
                    "arguments": {"path": "digest/a.md", "depth": 2},
                },
            }
        )
    )
    dream_response = require_response(
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "consolidate_memory",
                    "arguments": {"date": "2026-08-17", "hint": "h"},
                },
            }
        )
    )
    proactive_response = require_response(
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "get_proactive_topics",
                    "arguments": {"date": "2026-08-17"},
                },
            }
        )
    )

    assert read_response["result"]["structuredContent"]["answer"] == "read answer"
    assert trace_response["result"]["structuredContent"]["answer"] == "trace answer"
    assert dream_response["result"]["structuredContent"]["answer"] == "dream answer"
    assert proactive_response["result"]["structuredContent"]["answer"] == "topic answer"
