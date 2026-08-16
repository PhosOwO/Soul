from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Event:
    id: int | None
    type: str
    entity_id: int | None = None
    reason: str | None = None
    source: str | None = None
    change: dict[str, Any] | None = None
