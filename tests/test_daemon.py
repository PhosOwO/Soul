from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from soul.services.integrations.queue import enqueue_turn_evidence
from soul.services.project_resolver import register_project
from soul.services.daemon import (
    notification_candidates,
    notification_state_path,
    notify_review_index,
    send_desktop_notification,
    write_review_index,
)
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
    assert (soul_home / "review_index.json").is_file()
    review_index = json.loads((soul_home / "review_index.json").read_text(encoding="utf-8"))
    assert review_index["projects"][0]["review"]["needs_review"] == 2
    registry = json.loads((soul_home / "projects.json").read_text(encoding="utf-8"))
    assert registry["projects"][0]["status"] == "active"
    assert registry["projects"][0]["last_scanned_at"]
    assert registry["projects"][0]["review"]["total"] == 2

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


def test_daemon_scan_marks_missing_project_unavailable(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    missing_project = tmp_path / "missing"
    register_project(missing_project, project_name="Missing")

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "daemon", "scan", "--json"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["projects"][0]["available"] is False
    assert payload["projects"][0]["status"] == "unavailable"
    assert payload["projects"][0]["error"] == "project directory does not exist"
    registry = json.loads((soul_home / "projects.json").read_text(encoding="utf-8"))
    assert registry["projects"][0]["status"] == "unavailable"
    assert registry["projects"][0]["unavailable_since"]


def review_index_payload(*, review_total: int = 1, needs_review: int = 1, ready_to_confirm: int = 0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": "2026-08-26T00:00:00Z",
        "project_count": 1,
        "projects": [
            {
                "project_id": "project-1",
                "project_name": "Project",
                "project_dir": "/tmp/project",
                "available": True,
                "review": {
                    "total": review_total,
                    "needs_review": needs_review,
                    "ready_to_confirm": ready_to_confirm,
                    "available": review_total,
                },
                "queue": {"backlog": 0},
            }
        ],
    }


def test_daemon_notify_dry_run_reports_candidates_without_writing_state(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))

    result = notify_review_index(
        review_index_payload(),
        dry_run=True,
        now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        platform_name="darwin",
    )

    assert result["would_notify"] is True
    assert result["notifications"][0]["reason"] == "needs_review_increased"
    assert result["delivery"]["attempted"] is False
    assert not notification_state_path().exists()


def test_macos_notification_uses_osascript(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("soul.services.daemon.subprocess.run", fake_run)

    result = send_desktop_notification("Soul Review", "Project has 1 item", platform_name="darwin")

    assert result == {"attempted": True, "delivered": True, "platform": "darwin"}
    assert calls[0][0][0] == "osascript"
    assert calls[0][0][1] == "-e"
    assert 'display notification "Project has 1 item" with title "Soul Review"' == calls[0][0][2]
    assert calls[0][1]["capture_output"] is True


def test_daemon_notify_does_not_repeat_same_signature_inside_cooldown(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    deliveries = []

    def fake_send(title, body, *, platform_name=None):
        deliveries.append((title, body, platform_name))
        return {"attempted": True, "delivered": True, "platform": platform_name or "darwin"}

    monkeypatch.setattr("soul.services.daemon.send_desktop_notification", fake_send)
    index = review_index_payload()

    first = notify_review_index(index, now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC), platform_name="darwin")
    second = notify_review_index(index, now=datetime(2026, 8, 26, 12, 10, tzinfo=UTC), platform_name="darwin")

    assert first["would_notify"] is True
    assert second["would_notify"] is False
    assert len(deliveries) == 1
    state = json.loads(notification_state_path().read_text(encoding="utf-8"))
    assert state["projects"]["project-1"]["last_signature"] == "needs=1;ready=0;backlog=0"


def test_non_macos_notification_is_skipped(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))

    result = notify_review_index(
        review_index_payload(),
        now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        platform_name="linux",
    )

    assert result["would_notify"] is True
    assert result["delivery"]["attempted"] is False
    assert result["delivery"]["skipped"] is True
    assert result["delivery"]["platform"] == "linux"


def test_queue_backlog_candidate_requires_age_threshold():
    fresh = {
        "schema_version": 1,
        "projects": [
            {
                "project_id": "project-1",
                "project_name": "Project",
                "available": True,
                "review": {"total": 0, "needs_review": 0, "ready_to_confirm": 0},
                "queue": {"backlog": 1, "oldest_backlog_at": "2026-08-26T11:45:00Z"},
            }
        ],
    }
    stale = {
        **fresh,
        "projects": [
            {
                **fresh["projects"][0],
                "queue": {"backlog": 1, "oldest_backlog_at": "2026-08-26T11:20:00Z"},
            }
        ],
    }

    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

    assert notification_candidates(fresh, {"projects": {}}, now=now) == []
    candidates = notification_candidates(stale, {"projects": {}}, now=now)
    assert candidates[0]["reason"] == "queue_backlog_stale"


def test_daemon_notify_cli_dry_run_json_reads_review_index(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    write_review_index(review_index_payload())

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "daemon", "notify", "--dry-run", "--json"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["dry_run"] is True
    assert payload["would_notify"] is True
    assert payload["notifications"][0]["project_id"] == "project-1"
    assert not (soul_home / "notification_state.json").exists()
