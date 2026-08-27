from __future__ import annotations

import json
import os
import plistlib
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
    scan_registered_projects,
    send_desktop_notification,
    should_scan_project_record,
    write_review_index,
)
from soul.services.background.macos import (
    build_macos_launch_agent_plist,
    install_macos_launch_agent,
    macos_launch_agent_path,
    macos_launch_agent_status,
    uninstall_macos_launch_agent,
)
from soul.services.background.windows import WindowsBackgroundService
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
        [sys.executable, "-m", "soul.cli", "scan", "--json"],
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
    assert not (soul_home / "daemon_status.json").exists()
    assert (soul_home / "review_index.json").is_file()
    review_index = json.loads((soul_home / "review_index.json").read_text(encoding="utf-8"))
    assert review_index["projects"][0]["review"]["needs_review"] == 2
    registry = json.loads((soul_home / "projects.json").read_text(encoding="utf-8"))
    assert registry["projects"][0]["status"] == "active"
    assert registry["projects"][0]["identity"] == {"kind": "path_hash", "source": str(project.resolve())}
    assert registry["projects"][0]["reme_root"] == str(project.resolve() / ".soul" / "reme")
    assert registry["projects"][0]["traces_root"] == str(project.resolve() / ".soul" / "traces")
    assert registry["projects"][0]["last_scanned_at"]
    assert registry["projects"][0]["review"]["total"] == 2

    status = subprocess.run(
        [sys.executable, "-m", "soul.cli", "scan", "status"],
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


def test_daemon_scan_records_next_lifecycle_boundary(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "project"
    project.mkdir()
    load_state(project, project_name="Project")
    review_after = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    expires_at = (datetime.now(UTC) + timedelta(hours=6)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    upsert_working_state_from_evidence(
        project,
        {
            "source": "test",
            "task": "future lifecycle",
            "summary": "未来需要 review。",
            "evidence_refs": [{"type": "reme_file", "path": "daily/future.md"}],
            "working_state": {
                "route": "working_state",
                "statement": "未来需要 review。",
                "reason": "验证生命周期扫描边界。",
                "scope": "lifecycle",
                "review_after": review_after,
                "expires": expires_at,
            },
        },
    )
    register_project(project, project_name="Project")

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "scan", "--json"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["projects"][0]["lifecycle"]["next_lifecycle_scan_at"] == review_after
    registry = json.loads((soul_home / "projects.json").read_text(encoding="utf-8"))
    assert registry["projects"][0]["next_lifecycle_scan_at"] == review_after


def test_daemon_due_only_scan_skips_projects_before_lifecycle_boundary(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "project"
    project.mkdir()
    load_state(project, project_name="Project")
    record = register_project(project, project_name="Project")
    future = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record["next_lifecycle_scan_at"] = future
    record["review"] = {"total": 0, "needs_review": 0, "ready_to_confirm": 0}
    record["queue"] = {"backlog": 0}
    from soul.services.project_resolver import update_project_registry_records

    update_project_registry_records([record])

    status = scan_registered_projects(due_only=True)

    assert status["projects"][0]["scan_skipped"] is True
    assert status["projects"][0]["lifecycle"]["next_lifecycle_scan_at"] == future


def test_lifecycle_scan_keeps_projects_with_pending_work_due():
    future = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    assert should_scan_project_record(
        {
            "status": "active",
            "next_lifecycle_scan_at": future,
            "review": {"total": 1},
            "queue": {"backlog": 0},
        },
        now=datetime.now(UTC),
    )
    assert should_scan_project_record(
        {
            "status": "active",
            "next_lifecycle_scan_at": future,
            "review": {"total": 0},
            "queue": {"backlog": 1},
        },
        now=datetime.now(UTC),
    )


def test_daemon_scan_marks_missing_project_unavailable(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    missing_project = tmp_path / "missing"
    register_project(missing_project, project_name="Missing")

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "scan", "--json"],
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
    assert result["reason"] == "notification_candidates"
    assert result["notifications"][0]["reason"] == "needs_review_increased"
    assert result["delivery"]["attempted"] is False
    assert not notification_state_path().exists()


def test_macos_notification_uses_osascript(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("soul.services.notifications.macos.subprocess.run", fake_run)

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


def test_daemon_notify_does_not_repeat_same_signature_after_cooldown(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    deliveries = []

    def fake_send(title, body, *, platform_name=None):
        deliveries.append((title, body, platform_name))
        return {"attempted": True, "delivered": True, "platform": platform_name or "darwin"}

    monkeypatch.setattr("soul.services.daemon.send_desktop_notification", fake_send)
    index = review_index_payload(review_total=3, needs_review=3)

    first = notify_review_index(index, now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC), platform_name="darwin")
    second = notify_review_index(index, now=datetime(2026, 8, 26, 15, 0, tzinfo=UTC), platform_name="darwin")

    assert first["would_notify"] is True
    assert second["would_notify"] is False
    assert second["reason"] == "project_signature_unchanged"
    assert len(deliveries) == 1


def test_queue_backlog_does_not_repeat_when_unchanged_after_notification(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    deliveries = []
    index = {
        "schema_version": 1,
        "projects": [
            {
                "project_id": "project-1",
                "project_name": "Project",
                "available": True,
                "review": {"total": 0, "needs_review": 0, "ready_to_confirm": 0},
                "queue": {"backlog": 1, "oldest_backlog_at": "2026-08-26T11:20:00Z"},
            }
        ],
    }

    def fake_send(title, body, *, platform_name=None):
        deliveries.append((title, body, platform_name))
        return {"attempted": True, "delivered": True, "platform": platform_name or "darwin"}

    monkeypatch.setattr("soul.services.daemon.send_desktop_notification", fake_send)

    first = notify_review_index(index, now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC), platform_name="darwin")
    second = notify_review_index(index, now=datetime(2026, 8, 26, 15, 0, tzinfo=UTC), platform_name="darwin")

    assert first["notifications"][0]["reason"] == "queue_backlog_stale"
    assert second["would_notify"] is False
    assert second["reason"] == "project_signature_unchanged"
    assert len(deliveries) == 1


def test_notify_captures_desktop_delivery_exception(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))

    def fail_send(title, body, *, platform_name=None):
        raise RuntimeError("notification transport failed")

    monkeypatch.setattr("soul.services.daemon.send_desktop_notification", fail_send)

    result = notify_review_index(
        review_index_payload(),
        now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        platform_name="darwin",
    )

    assert result["would_notify"] is True
    assert result["delivery"]["attempted"] is True
    assert result["delivery"]["delivered"] is False
    assert result["delivery"]["error"] == "notification transport failed"
    assert notification_state_path().exists()


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


def test_notify_dry_run_without_candidates_reports_stable_reason(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))

    result = notify_review_index(
        review_index_payload(review_total=0, needs_review=0),
        dry_run=True,
        now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        platform_name="darwin",
    )

    assert result["would_notify"] is False
    assert result["reason"] == "no_reviewable_notification_candidates"
    assert result["notifications"] == []
    assert result["delivery"]["attempted"] is False


def test_daemon_notify_cli_dry_run_json_reads_review_index(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    write_review_index(review_index_payload())

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "scan", "notify", "--dry-run", "--json"],
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


def test_daemon_status_cli_reads_review_index_not_legacy_status(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    write_review_index(review_index_payload(review_total=3, needs_review=2, ready_to_confirm=1))
    (soul_home / "daemon_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "legacy",
                "project_count": 1,
                "projects": [
                    {
                        "project_id": "legacy",
                        "project_name": "Legacy",
                        "available": True,
                        "review": {"total": 99, "needs_review": 99, "ready_to_confirm": 0},
                        "queue": {"backlog": 0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "scan", "status", "--json"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["projects"][0]["project_id"] == "project-1"
    assert payload["projects"][0]["review"]["total"] == 3


def test_macos_launch_agent_plist_runs_daemon_loop(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))

    plist = build_macos_launch_agent_plist(interval_seconds=123)

    assert plist["Label"] == "com.soulkit.daemon"
    assert plist["ProgramArguments"][:5] == [sys.executable, "-m", "soul.cli", "scan", "run"]
    assert plist["ProgramArguments"][-1] == "123"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["EnvironmentVariables"]["SOUL_HOME"] == str(soul_home.resolve())
    assert plist["StandardOutPath"] == str(soul_home.resolve() / "logs" / "daemon.log")


def test_macos_daemon_install_writes_plist_and_bootstraps(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    home = tmp_path / "home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    calls = []

    def fake_run_launchctl(args):
        calls.append(args)
        return subprocess.CompletedProcess(["launchctl", *args], 0, "", "")

    monkeypatch.setattr("soul.services.background.macos.run_launchctl", fake_run_launchctl)

    result = install_macos_launch_agent(interval_seconds=321, platform_name="darwin", home_dir=home, user_id=501)

    plist_path = macos_launch_agent_path(home_dir=home)
    assert result["ok"] is True
    assert result["loaded"] is True
    assert result["plist_path"] == str(plist_path)
    assert plist_path.is_file()
    assert calls[0][0] == "bootout"
    assert calls[1] == ["bootstrap", "gui/501", str(plist_path)]
    assert b"<string>321</string>" in plist_path.read_bytes()


def test_macos_daemon_uninstall_removes_plist_and_boots_out(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plist_path = macos_launch_agent_path(home_dir=home)
    plist_path.parent.mkdir(parents=True)
    plist_path.write_text("plist", encoding="utf-8")
    calls = []

    def fake_run_launchctl(args):
        calls.append(args)
        return subprocess.CompletedProcess(["launchctl", *args], 0, "", "")

    monkeypatch.setattr("soul.services.background.macos.run_launchctl", fake_run_launchctl)

    result = uninstall_macos_launch_agent(platform_name="darwin", home_dir=home, user_id=501)

    assert result["ok"] is True
    assert result["removed"] is True
    assert not plist_path.exists()
    assert calls == [["bootout", "gui/501", str(plist_path)]]


def test_macos_launch_agent_status_reports_loaded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plist_path = macos_launch_agent_path(home_dir=home)
    plist_path.parent.mkdir(parents=True)
    plist_path.write_text("plist", encoding="utf-8")

    def fake_run_launchctl(args):
        assert args == ["print", "gui/501/com.soulkit.daemon"]
        return subprocess.CompletedProcess(["launchctl", *args], 0, "running", "")

    monkeypatch.setattr("soul.services.background.macos.run_launchctl", fake_run_launchctl)

    result = macos_launch_agent_status(platform_name="darwin", home_dir=home, user_id=501)

    assert result["installed"] is True
    assert result["loaded"] is True
    assert result["launchctl"]["stdout"] == "running"


def test_daemon_command_is_not_supported(tmp_path):
    soul_home = tmp_path / "soul-home"

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "daemon", "status"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "invalid choice" in completed.stderr


def test_windows_background_install_no_load_is_structured(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))

    result = WindowsBackgroundService().install(interval_seconds=77, load=False)

    assert result["ok"] is True
    assert result["platform"] == "win32"
    assert result["task_name"] == "SoulKitDaemon"
    assert result["loaded"] is False
    assert Path(str(result["script_path"])).is_file()
    assert "soul.cli scan run" in Path(str(result["script_path"])).read_text(encoding="utf-8")


def test_scan_run_once_cli_refreshes_review_index(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "project"
    project.mkdir()
    load_state(project, project_name="Scan Run")
    register_project(project, project_name="Scan Run")

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "scan", "run", "--once"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout == ""
    review_index = json.loads((soul_home / "review_index.json").read_text(encoding="utf-8"))
    assert review_index["projects"][0]["project_name"] == "Scan Run"


def test_service_install_cli_no_load_json_uses_background_adapter(tmp_path):
    soul_home = tmp_path / "soul-home"
    home = tmp_path / "home"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "soul.cli",
            "service",
            "install",
            "--no-load",
            "--interval-seconds",
            "77",
            "--json",
        ],
        cwd=ROOT,
        env={**cli_env(soul_home), "HOME": str(home)},
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["ok"] is True
    assert payload["loaded"] is False
    if payload["platform"] == "darwin":
        assert Path(payload["plist_path"]).is_file()
        plist = plistlib.loads(Path(payload["plist_path"]).read_bytes())
        assert plist["ProgramArguments"][:5] == [sys.executable, "-m", "soul.cli", "scan", "run"]
    else:
        assert payload["platform"] == sys.platform
        assert payload["capability"] == "background_service"


def test_service_status_cli_json_is_structured(tmp_path):
    soul_home = tmp_path / "soul-home"

    completed = subprocess.run(
        [sys.executable, "-m", "soul.cli", "service", "status", "--json"],
        cwd=ROOT,
        env=cli_env(soul_home),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["platform"] == sys.platform
    assert payload["capability"] == "background_service"
