from __future__ import annotations

import sys

from soul.services.background.macos import MacOSBackgroundService
from soul.services.background.types import BackgroundService
from soul.services.background.unsupported import UnsupportedBackgroundService
from soul.services.background.windows import WindowsBackgroundService


def background_service_for_platform(platform_name: str | None = None) -> BackgroundService:
    platform = platform_name or sys.platform
    if platform == "darwin":
        return MacOSBackgroundService()
    if platform == "win32":
        return WindowsBackgroundService()
    return UnsupportedBackgroundService(platform_name=platform)
