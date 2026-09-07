from __future__ import annotations

from typing import Any


class WindowsNotifier:
    def send(self, title: str, body: str, *, action: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "attempted": False,
            "delivered": False,
            "skipped": True,
            "platform": "win32",
            "capability": "desktop_notification",
            "reason": "windows toast notification is not implemented yet",
        }
