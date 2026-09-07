from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


NOTIFICATION_APP_NAME = "SoulNotifier.app"
NOTIFICATION_WAIT_SECONDS = 120


class MacOSNotifier:
    def send(self, title: str, body: str, *, action: dict[str, Any] | None = None) -> dict[str, Any]:
        if action and action.get("kind") == "review":
            result = self.send_clickable_review_notification(title, body, action=action)
            if result.get("delivered"):
                return result
        script = f"display notification {applescript_string(body)} with title {applescript_string(title)}"
        try:
            completed = subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)
        except OSError as exc:
            return {"attempted": True, "delivered": False, "platform": "darwin", "error": str(exc)}
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "").strip()
            return {"attempted": True, "delivered": False, "platform": "darwin", "error": error}
        return {"attempted": True, "delivered": True, "platform": "darwin"}

    def send_clickable_review_notification(self, title: str, body: str, *, action: dict[str, Any]) -> dict[str, Any]:
        try:
            app_path = ensure_notification_app(title=title, body=body, action=action)
            completed = subprocess.run(
                ["open", "-gj", str(app_path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return {
                "attempted": True,
                "delivered": False,
                "platform": "darwin",
                "capability": "clickable_review_notification",
                "error": str(exc),
            }
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "").strip()
            return {
                "attempted": True,
                "delivered": False,
                "platform": "darwin",
                "capability": "clickable_review_notification",
                "error": error,
            }
        return {
            "attempted": True,
            "delivered": True,
            "platform": "darwin",
            "capability": "clickable_review_notification",
            "action": {"kind": "review", "url": action.get("url")},
        }


def ensure_notification_app(*, title: str, body: str, action: dict[str, Any]) -> Path:
    app_path = Path(tempfile.gettempdir()) / "soulkit" / NOTIFICATION_APP_NAME
    script_path = app_path / "Contents" / "Resources" / "Scripts" / "main.scpt"
    source_stamp_path = app_path / "Contents" / "Resources" / "soul-notifier.applescript"
    source = notification_app_source(title=title, body=body, action=action)
    if script_path.exists() and source_stamp_path.exists():
        try:
            if source_stamp_path.read_text(encoding="utf-8") == source:
                return app_path
        except OSError:
            pass
    app_path.parent.mkdir(parents=True, exist_ok=True)
    source_path = app_path.parent / "SoulNotifier.applescript"
    source_path.write_text(source, encoding="utf-8")
    if app_path.exists():
        shutil.rmtree(app_path)
    completed = subprocess.run(["osacompile", "-o", str(app_path), str(source_path)], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "").strip()
        raise OSError(error or "failed to compile Soul notification app")
    source_stamp_path.write_text(source, encoding="utf-8")
    return app_path


def notification_app_source(*, title: str, body: str, action: dict[str, Any]) -> str:
    command = review_action_shell_command(action)
    marker_path = notification_marker_path()
    return f"""
property markerPath : {applescript_string(str(marker_path))}
property reviewCommand : {applescript_string(command)}
property cleanupCommand : {applescript_string(notification_marker_cleanup_command(marker_path))}

on run
    try
        do shell script "test -f " & quoted form of markerPath
        do shell script "rm -f " & quoted form of markerPath
        do shell script reviewCommand
    on error
        do shell script "mkdir -p " & quoted form of {applescript_string(str(marker_path.parent))}
        do shell script "date > " & quoted form of markerPath
        do shell script cleanupCommand
        display notification {applescript_string(body)} with title {applescript_string(title)}
    end try
    quit
end run
""".strip()


def notification_marker_path() -> Path:
    return Path(tempfile.gettempdir()) / "soulkit" / "review-notification-armed"


def notification_marker_cleanup_command(marker_path: Path) -> str:
    return f"(sleep {NOTIFICATION_WAIT_SECONDS}; rm -f {shlex.quote(str(marker_path))}) >/dev/null 2>&1 &"


def review_action_shell_command(action: dict[str, Any]) -> str:
    project_dir = str(action.get("project_dir") or ".")
    host = str(action.get("host") or "127.0.0.1")
    port = str(action.get("port") or "8765")
    python = str(action.get("python") or sys.executable)
    env_parts = [
        f"PATH={shlex.quote(os.environ.get('PATH', ''))}",
    ]
    pythonpath = os.environ.get("PYTHONPATH")
    if pythonpath:
        env_parts.append(f"PYTHONPATH={shlex.quote(pythonpath)}")
    command = [
        python,
        "-m",
        "soul.cli",
        "--review",
        "--review-project-dir",
        project_dir,
        "--review-host",
        host,
        "--review-port",
        port,
    ]
    return " ".join([*env_parts, *[shlex.quote(part) for part in command]]) + " >/dev/null 2>&1 &"


def applescript_string(value: str) -> str:
    return json.dumps(value)
