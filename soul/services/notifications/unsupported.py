from __future__ import annotations

from typing import Any


class UnsupportedNotifier:
    def __init__(self, *, platform_name: str) -> None:
        self.platform_name = platform_name

    def send(self, title: str, body: str, *, action: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "attempted": False,
            "delivered": False,
            "skipped": True,
            "platform": self.platform_name,
            "capability": "desktop_notification",
        }
