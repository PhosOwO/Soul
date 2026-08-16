from __future__ import annotations

import json

from soul.mcp import SoulMcpServer
from soul.storage.database import connect, default_db_path, init_database


def test_mcp_lists_soul_tools(tmp_path):
    with connect(default_db_path(tmp_path)) as conn:
        init_database(conn, project_name="Demo")

    response = SoulMcpServer(tmp_path).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    tools = response["result"]["tools"]
    assert {tool["name"] for tool in tools} >= {
        "get_projected_state",
        "observe_evidence",
        "propose_patch",
        "apply_patch",
    }


def test_mcp_get_projected_state_returns_tool_content(tmp_path):
    with connect(default_db_path(tmp_path)) as conn:
        init_database(conn, project_name="Demo")

    response = SoulMcpServer(tmp_path).handle(
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

    result = response["result"]
    payload = json.loads(result["content"][0]["text"])
    assert "Soul Current State" in payload["injection"]
    assert result["structuredContent"]["task"] == "What should we do next?"


def test_mcp_observe_evidence_proposes_patch_without_applying(tmp_path):
    with connect(default_db_path(tmp_path)) as conn:
        init_database(conn, project_name="Demo")

    response = SoulMcpServer(tmp_path).handle(
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
                },
            },
        }
    )

    payload = response["result"]["structuredContent"]
    assert payload["patch_proposal"]["status"] == "proposed"
    assert payload["patch_proposal"]["review_recommendation"] == "needs_review"
