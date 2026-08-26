from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from soul.services.reme.runtime_config import soul_home


MACOS_DAEMON_LABEL = "com.soulkit.daemon"
MACOS_LAUNCH_AGENT_NAME = MACOS_DAEMON_LABEL + ".plist"
DEFAULT_BACKGROUND_INTERVAL_SECONDS = 900


def package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def daemon_log_dir() -> Path:
    return soul_home() / "logs"


def macos_launch_agent_path(*, home_dir: Path | None = None) -> Path:
    return (home_dir or Path.home()) / "Library" / "LaunchAgents" / MACOS_LAUNCH_AGENT_NAME


def current_user_id() -> int:
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise RuntimeError("macOS launchd user id is unavailable on this platform")
    return int(getuid())


def macos_launchd_domain(*, user_id: int | None = None) -> str:
    return f"gui/{current_user_id() if user_id is None else user_id}"


def build_macos_launch_agent_plist(*, interval_seconds: float = DEFAULT_BACKGROUND_INTERVAL_SECONDS) -> dict[str, Any]:
    logs_dir = daemon_log_dir()
    pythonpath = str(package_root())
    current_pythonpath = os.environ.get("PYTHONPATH")
    if current_pythonpath:
        pythonpath = pythonpath + os.pathsep + current_pythonpath
    return {
        "Label": MACOS_DAEMON_LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "soul.cli",
            "daemon",
            "run",
            "--interval-seconds",
            str(interval_seconds),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(package_root()),
        "StandardOutPath": str(logs_dir / "daemon.log"),
        "StandardErrorPath": str(logs_dir / "daemon.err.log"),
        "EnvironmentVariables": {
            "PYTHONPATH": pythonpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOUL_HOME": str(soul_home()),
        },
    }


class MacOSBackgroundService:
    def __init__(self, *, home_dir: Path | None = None, user_id: int | None = None) -> None:
        self.home_dir = home_dir
        self.user_id = user_id

    def install(self, *, interval_seconds: float = DEFAULT_BACKGROUND_INTERVAL_SECONDS, load: bool = True) -> dict[str, Any]:
        plist_path = macos_launch_agent_path(home_dir=self.home_dir)
        plist = build_macos_launch_agent_plist(interval_seconds=interval_seconds)
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        daemon_log_dir().mkdir(parents=True, exist_ok=True)
        with plist_path.open("wb") as stream:
            plistlib.dump(plist, stream, sort_keys=False)
        result: dict[str, Any] = {
            "ok": True,
            "platform": "darwin",
            "capability": "background_service",
            "label": MACOS_DAEMON_LABEL,
            "plist_path": str(plist_path),
            "loaded": False,
        }
        if load:
            bootout = run_launchctl(["bootout", macos_launchd_domain(user_id=self.user_id), str(plist_path)])
            bootstrap = run_launchctl(["bootstrap", macos_launchd_domain(user_id=self.user_id), str(plist_path)])
            result["bootout"] = launchctl_result(bootout)
            result["bootstrap"] = launchctl_result(bootstrap)
            result["loaded"] = bootstrap.returncode == 0
            result["ok"] = bootstrap.returncode == 0
        return result

    def start(self) -> dict[str, Any]:
        plist_path = macos_launch_agent_path(home_dir=self.home_dir)
        if not plist_path.exists():
            return {
                "ok": False,
                "platform": "darwin",
                "capability": "background_service",
                "label": MACOS_DAEMON_LABEL,
                "plist_path": str(plist_path),
                "error": "launch agent is not installed",
            }
        bootstrap = run_launchctl(["bootstrap", macos_launchd_domain(user_id=self.user_id), str(plist_path)])
        return {
            "ok": bootstrap.returncode == 0,
            "platform": "darwin",
            "capability": "background_service",
            "label": MACOS_DAEMON_LABEL,
            "plist_path": str(plist_path),
            "loaded": bootstrap.returncode == 0,
            "bootstrap": launchctl_result(bootstrap),
        }

    def stop(self) -> dict[str, Any]:
        plist_path = macos_launch_agent_path(home_dir=self.home_dir)
        bootout = run_launchctl(["bootout", macos_launchd_domain(user_id=self.user_id), str(plist_path)])
        return {
            "ok": bootout.returncode == 0,
            "platform": "darwin",
            "capability": "background_service",
            "label": MACOS_DAEMON_LABEL,
            "plist_path": str(plist_path),
            "loaded": False,
            "bootout": launchctl_result(bootout),
        }

    def restart(self) -> dict[str, Any]:
        stop_result = self.stop()
        start_result = self.start()
        return {
            "ok": bool(start_result.get("ok")),
            "platform": "darwin",
            "capability": "background_service",
            "label": MACOS_DAEMON_LABEL,
            "plist_path": start_result.get("plist_path", stop_result.get("plist_path")),
            "loaded": bool(start_result.get("loaded", False)),
            "stop": stop_result,
            "start": start_result,
        }

    def uninstall(self) -> dict[str, Any]:
        plist_path = macos_launch_agent_path(home_dir=self.home_dir)
        bootout = run_launchctl(["bootout", macos_launchd_domain(user_id=self.user_id), str(plist_path)])
        removed = False
        try:
            if plist_path.exists():
                plist_path.unlink()
                removed = True
        except OSError as exc:
            return {
                "ok": False,
                "platform": "darwin",
                "capability": "background_service",
                "label": MACOS_DAEMON_LABEL,
                "plist_path": str(plist_path),
                "removed": removed,
                "bootout": launchctl_result(bootout),
                "error": str(exc),
            }
        return {
            "ok": bootout.returncode == 0 or removed,
            "platform": "darwin",
            "capability": "background_service",
            "label": MACOS_DAEMON_LABEL,
            "plist_path": str(plist_path),
            "removed": removed,
            "bootout": launchctl_result(bootout),
        }

    def status(self) -> dict[str, Any]:
        plist_path = macos_launch_agent_path(home_dir=self.home_dir)
        result: dict[str, Any] = {
            "platform": "darwin",
            "capability": "background_service",
            "label": MACOS_DAEMON_LABEL,
            "plist_path": str(plist_path),
            "installed": plist_path.exists(),
            "loaded": False,
        }
        status = run_launchctl(["print", f"{macos_launchd_domain(user_id=self.user_id)}/{MACOS_DAEMON_LABEL}"])
        result["launchctl"] = launchctl_result(status)
        result["loaded"] = status.returncode == 0
        return result


def install_macos_launch_agent(
    *,
    interval_seconds: float = DEFAULT_BACKGROUND_INTERVAL_SECONDS,
    load: bool = True,
    platform_name: str | None = None,
    home_dir: Path | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    platform = platform_name or sys.platform
    if platform != "darwin":
        return {"ok": False, "unsupported": True, "platform": platform, "label": MACOS_DAEMON_LABEL}
    return MacOSBackgroundService(home_dir=home_dir, user_id=user_id).install(interval_seconds=interval_seconds, load=load)


def start_macos_launch_agent(
    *,
    platform_name: str | None = None,
    home_dir: Path | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    platform = platform_name or sys.platform
    if platform != "darwin":
        return {"ok": False, "unsupported": True, "platform": platform, "label": MACOS_DAEMON_LABEL}
    return MacOSBackgroundService(home_dir=home_dir, user_id=user_id).start()


def stop_macos_launch_agent(
    *,
    platform_name: str | None = None,
    home_dir: Path | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    platform = platform_name or sys.platform
    if platform != "darwin":
        return {"ok": False, "unsupported": True, "platform": platform, "label": MACOS_DAEMON_LABEL}
    return MacOSBackgroundService(home_dir=home_dir, user_id=user_id).stop()


def restart_macos_launch_agent(
    *,
    platform_name: str | None = None,
    home_dir: Path | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    platform = platform_name or sys.platform
    if platform != "darwin":
        return {"ok": False, "unsupported": True, "platform": platform, "label": MACOS_DAEMON_LABEL}
    return MacOSBackgroundService(home_dir=home_dir, user_id=user_id).restart()


def uninstall_macos_launch_agent(
    *,
    platform_name: str | None = None,
    home_dir: Path | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    platform = platform_name or sys.platform
    if platform != "darwin":
        return {"ok": False, "unsupported": True, "platform": platform, "label": MACOS_DAEMON_LABEL}
    return MacOSBackgroundService(home_dir=home_dir, user_id=user_id).uninstall()


def macos_launch_agent_status(
    *,
    platform_name: str | None = None,
    home_dir: Path | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    platform = platform_name or sys.platform
    if platform != "darwin":
        return {
            "platform": platform,
            "label": MACOS_DAEMON_LABEL,
            "plist_path": str(macos_launch_agent_path(home_dir=home_dir)),
            "installed": False,
            "loaded": False,
            "unsupported": True,
        }
    return MacOSBackgroundService(home_dir=home_dir, user_id=user_id).status()


def run_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["launchctl", *args], check=False, capture_output=True, text=True)
    except OSError as exc:
        return subprocess.CompletedProcess(["launchctl", *args], 127, "", str(exc))


def launchctl_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }
