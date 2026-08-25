from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from soul.services.integrations.queue import enqueue_turn_evidence
from soul.services.project_resolver import register_project
from soul.services.state_core.state_store import load_state
from soul.services.state_core.working_state import upsert_working_state_from_evidence


ROOT = Path(__file__).resolve().parents[1]


def cli_env(soul_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SOUL_HOME"] = str(soul_home)
    return env


def test_daemon_scan_reports_due_review_candidates(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "project"
    project.mkdir()
    load_state(project, project_name="Project")
    upsert_working_state_from_evidence(
        project,
        {
            "source": "test",
            "task": "review due",
            "summary": "后续默认使用 pnpm。",
            "evidence_refs": [{"type": "reme_file", "path": "daily/review.md"}],
            "working_state": {
                "route": "working_state",
                "statement": "后续默认使用 pnpm。",
                "reason": "用户明确纠正包管理器。",
                "scope": "dependency setup",
                "review_after": "2000-01-01T00:00:00Z",
            },
        },
    )
    upsert_working_state_from_evidence(
        project,
        {
            "source": "test",
            "task": "expired due",
            "summary": "后续默认过期项仍进入 review。",
            "evidence_refs": [{"type": "reme_file", "path": "daily/expired.md"}],
            "working_state": {
                "route": "working_state",
                "statement": "后续默认过期项仍进入 review。",
                "reason": "过期只影响上下文，不影响待确认可见性。",
                "scope": "lifecycle",
                "review_after": "2000-01-01T00:00:00Z",
                "expires": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )
    enqueue_turn_evidence(
        project,
        source="test",
        session_id="session-1",
        turn_id="turn-1",
        payload={"task": "queued task", "outcome": "queued outcome"},
    )
    register_project(project, project_name="Project")

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "daemon", "scan", "--json"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["project_count"] == 1
    assert payload["projects"][0]["project_dir"] == str(project.resolve())
    assert payload["projects"][0]["review"]["needs_review"] == 2
    assert payload["projects"][0]["lifecycle"]["active_working"] == 1
    assert payload["projects"][0]["lifecycle"]["review_due"] == 2
    assert payload["projects"][0]["lifecycle"]["expired_unresolved"] == 1
    assert payload["projects"][0]["queue"]["backlog"] == 1
    assert (soul_home / "daemon_status.json").is_file()

    status = subprocess.run(
        [sys.executable, "-m", "soul.cli", "daemon", "status"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Soul Daemon" in status.stdout
    assert "Project: review=2" in status.stdout
    assert "working=1 active/2 due/1 expired" in status.stdout
    assert "queue=1 backlog" in status.stdout
