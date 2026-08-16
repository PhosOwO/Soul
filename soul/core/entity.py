from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Entity:
    id: int | None
    scope_id: int
    type: str
    name: str
    state: str = "accepted"
    data: dict[str, Any] | None = None
