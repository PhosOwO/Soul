from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from soul.services.reme.runtime_config import soul_home


WINDOWS_TASK_NAME = "SoulKitScanService"
LEGACY_WINDOWS_TASK_NAME = "SoulKitDaemon"
DEFAULT_BACKGROUND_INTERVAL_SECONDS = 900


def package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def service_log_dir() -> Path:
    return soul_home() / "logs"


def service_script_path() -> Path:
    return soul_home() / "background" / "soul-scan-service.cmd"


def legacy_service_script_path() -> Path:
    return soul_home() / "background" / "soul-daemon.cmd"


def write_service_script(*, interval_seconds: float) -> Path:
    script_path = service_script_path()
    logs_dir = service_log_dir()
    script_path.parent.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "@echo off",
        f'set "SOUL_HOME={soul_home()}"',
        f'set "PYTHONPATH={package_root()};%PYTHONPATH%"',
        'set "PYTHONDONTWRITEBYTECODE=1"',
        f'if not exist "{logs_dir}" mkdir "{logs_dir}"',
        (
            f'"{sys.executable}" -m soul.cli scan run --interval-seconds {interval_seconds} '
            f'>> "{logs_dir / "scan.log"}" 2>> "{logs_dir / "scan.err.log"}"'
        ),
        "",
    ]
    script_path.write_text("\n".join(lines), encoding="utf-8")
    return script_path


class WindowsBackgroundService:
    def install(self, *, interval_seconds: float = DEFAULT_BACKGROUND_INTERVAL_SECONDS, load: bool = True) -> dict[str, Any]:
        script_path = write_service_script(interval_seconds=interval_seconds)
        command = f'"{script_path}"'
        result: dict[str, Any] = {
            "ok": True,
            "platform": "win32",
            "capability": "background_service",
            "task_name": WINDOWS_TASK_NAME,
            "loaded": False,
            "command": command,
            "script_path": str(script_path),
            "log_path": str(service_log_dir() / "scan.log"),
        }
        if not load:
            return result
        create = run_schtasks(
            [
                "/Create",
                "/TN",
                WINDOWS_TASK_NAME,
                "/TR",
                command,
                "/SC",
                "ONLOGON",
                "/F",
            ]
        )
        result["create"] = schtasks_result(create)
        result["ok"] = create.returncode == 0
        if create.returncode == 0:
            start = run_schtasks(["/Run", "/TN", WINDOWS_TASK_NAME])
            result["start"] = schtasks_result(start)
            result["loaded"] = start.returncode == 0
        return result

    def start(self) -> dict[str, Any]:
        started = run_schtasks(["/Run", "/TN", WINDOWS_TASK_NAME])
        return {
            "ok": started.returncode == 0,
            "platform": "win32",
            "capability": "background_service",
            "task_name": WINDOWS_TASK_NAME,
            "loaded": started.returncode == 0,
            "start": schtasks_result(started),
        }

    def stop(self) -> dict[str, Any]:
        stopped = run_schtasks(["/End", "/TN", WINDOWS_TASK_NAME])
        return {
            "ok": stopped.returncode == 0,
            "platform": "win32",
            "capability": "background_service",
            "task_name": WINDOWS_TASK_NAME,
            "loaded": False,
            "stop": schtasks_result(stopped),
        }

    def restart(self) -> dict[str, Any]:
        stop_result = self.stop()
        start_result = self.start()
        return {
            "ok": bool(start_result.get("ok")),
            "platform": "win32",
            "capability": "background_service",
            "task_name": WINDOWS_TASK_NAME,
            "loaded": bool(start_result.get("loaded", False)),
            "stop": stop_result,
            "start": start_result,
        }

    def uninstall(self) -> dict[str, Any]:
        deleted = run_schtasks(["/Delete", "/TN", WINDOWS_TASK_NAME, "/F"])
        legacy_deleted = run_schtasks(["/Delete", "/TN", LEGACY_WINDOWS_TASK_NAME, "/F"])
        script_path = service_script_path()
        legacy_script_path = legacy_service_script_path()
        script_removed = remove_file_if_exists(script_path)
        legacy_script_removed = remove_file_if_exists(legacy_script_path)
        return {
            "ok": deleted.returncode == 0
            or legacy_deleted.returncode == 0
            or script_removed
            or legacy_script_removed,
            "platform": "win32",
            "capability": "background_service",
            "task_name": WINDOWS_TASK_NAME,
            "removed": deleted.returncode == 0,
            "delete": schtasks_result(deleted),
            "script_path": str(script_path),
            "script_removed": script_removed,
            "legacy_task_name": LEGACY_WINDOWS_TASK_NAME,
            "legacy_removed": legacy_deleted.returncode == 0,
            "legacy_delete": schtasks_result(legacy_deleted),
            "legacy_script_path": str(legacy_script_path),
            "legacy_script_removed": legacy_script_removed,
        }

    def status(self) -> dict[str, Any]:
        query = run_schtasks(["/Query", "/TN", WINDOWS_TASK_NAME])
        legacy_query = run_schtasks(["/Query", "/TN", LEGACY_WINDOWS_TASK_NAME])
        return {
            "platform": "win32",
            "capability": "background_service",
            "task_name": WINDOWS_TASK_NAME,
            "installed": query.returncode == 0,
            "loaded": False,
            "schtasks": schtasks_result(query),
            "legacy_task_name": LEGACY_WINDOWS_TASK_NAME,
            "legacy_installed": legacy_query.returncode == 0,
            "legacy_schtasks": schtasks_result(legacy_query),
        }


def run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    try:
        return subprocess.run(["schtasks.exe", *args], check=False, capture_output=True, text=True, env=env)
    except OSError as exc:
        return subprocess.CompletedProcess(["schtasks.exe", *args], 127, "", str(exc))


def remove_file_if_exists(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def schtasks_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }
