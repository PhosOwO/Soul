from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any


NOTIFICATION_BINARY_NAME = "SoulNotifier"
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
            binary_path = ensure_notification_binary()
            subprocess.Popen(
                [
                    str(binary_path),
                    "--title",
                    title,
                    "--body",
                    body,
                    "--command",
                    review_action_shell_command(action),
                    "--timeout",
                    str(NOTIFICATION_WAIT_SECONDS),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            return {
                "attempted": True,
                "delivered": False,
                "platform": "darwin",
                "capability": "clickable_review_notification",
                "error": str(exc),
            }
        return {
            "attempted": True,
            "delivered": True,
            "platform": "darwin",
            "capability": "clickable_review_notification",
            "action": {"kind": "review", "url": action.get("url")},
        }


def ensure_notification_binary() -> Path:
    binary_path = Path(tempfile.gettempdir()) / "soulkit" / NOTIFICATION_BINARY_NAME
    source_path = binary_path.with_suffix(".swift")
    source = notification_binary_source()
    if binary_path.exists() and source_path.exists():
        try:
            if source_path.read_text(encoding="utf-8") == source:
                return binary_path
        except OSError:
            pass
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    if binary_path.exists():
        binary_path.unlink()
    completed = subprocess.run(["swiftc", str(source_path), "-o", str(binary_path)], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "").strip()
        raise OSError(error or "failed to compile Soul notification helper")
    return binary_path


def notification_binary_source() -> str:
    return resources.files("soul.templates").joinpath("macos_notifier.swift").read_text(encoding="utf-8")


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
