from __future__ import annotations

from typing import Any, Protocol


class Notifier(Protocol):
    def send(self, title: str, body: str) -> dict[str, Any]: ...
