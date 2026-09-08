from __future__ import annotations

import json
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any


NOTIFICATION_APP_NAME = "SoulNotifier.app"
NOTIFICATION_EXECUTABLE_NAME = "SoulNotifier"
NOTIFICATION_WAIT_SECONDS = 120


class MacOSNotifier:
    def send(self, title: str, body: str, *, action: dict[str, Any] | None = None) -> dict[str, Any]:
        if action and action.get("kind") == "review":
            result = self.send_clickable_review_notification(title, body, action=action)
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
            app_path = ensure_notification_app()
            completed = subprocess.run(
                [
                    "open",
                    "-gj",
                    str(app_path),
                    "--args",
                    "--title",
                    title,
                    "--body",
                    body,
                    "--command",
                    review_action_shell_command(action),
                    "--timeout",
                    str(NOTIFICATION_WAIT_SECONDS),
                ],
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


def ensure_notification_app() -> Path:
    app_path = Path(tempfile.gettempdir()) / "soulkit" / NOTIFICATION_APP_NAME
    executable_path = app_path / "Contents" / "MacOS" / NOTIFICATION_EXECUTABLE_NAME
    source_path = app_path.parent / f"{NOTIFICATION_EXECUTABLE_NAME}.swift"
    source_stamp_path = app_path / "Contents" / "Resources" / "soul-notifier.swift"
    source = notification_binary_source()
    if executable_path.exists() and source_stamp_path.exists():
        try:
            if source_stamp_path.read_text(encoding="utf-8") == source:
                return app_path
        except OSError:
            pass
    executable_path.parent.mkdir(parents=True, exist_ok=True)
    source_stamp_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    if executable_path.exists():
        executable_path.unlink()
    completed = subprocess.run(["swiftc", str(source_path), "-o", str(executable_path)], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "").strip()
        raise OSError(error or "failed to compile Soul notification helper")
    write_notification_app_info_plist(app_path)
    source_stamp_path.write_text(source, encoding="utf-8")
    return app_path


def write_notification_app_info_plist(app_path: Path) -> None:
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": NOTIFICATION_EXECUTABLE_NAME,
        "CFBundleIdentifier": "com.soulkit.review-notifier",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Soul Review",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "11.0",
        "LSUIElement": True,
    }
    with (app_path / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream, sort_keys=False)


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
