from __future__ import annotations

import sys

from soul.services.notifications.macos import MacOSNotifier
from soul.services.notifications.types import Notifier
from soul.services.notifications.unsupported import UnsupportedNotifier
from soul.services.notifications.windows import WindowsNotifier


def notifier_for_platform(platform_name: str | None = None) -> Notifier:
    platform = platform_name or sys.platform
    if platform == "darwin":
        return MacOSNotifier()
    if platform == "win32":
        return WindowsNotifier()
    return UnsupportedNotifier(platform_name=platform)
