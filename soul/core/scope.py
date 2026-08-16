from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Scope:
    id: int | None
    name: str
    type: str = "project"
    parent_id: int | None = None
    data: dict[str, Any] | None = None
