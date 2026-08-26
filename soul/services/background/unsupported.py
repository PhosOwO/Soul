from __future__ import annotations

from typing import Any


class UnsupportedBackgroundService:
    def __init__(self, *, platform_name: str) -> None:
        self.platform_name = platform_name

    def _result(self, action: str) -> dict[str, Any]:
        return {
            "ok": False,
            "unsupported": True,
            "platform": self.platform_name,
            "capability": "background_service",
            "action": action,
        }

    def install(self, *, interval_seconds: float, load: bool = True) -> dict[str, Any]:
        result = self._result("install")
        result["interval_seconds"] = interval_seconds
        result["loaded"] = False
        return result

    def start(self) -> dict[str, Any]:
        return self._result("start")

    def stop(self) -> dict[str, Any]:
        return self._result("stop")

    def restart(self) -> dict[str, Any]:
        return self._result("restart")

    def uninstall(self) -> dict[str, Any]:
        return self._result("uninstall")

    def status(self) -> dict[str, Any]:
        result = self._result("status")
        result["installed"] = False
        result["loaded"] = False
        return result
