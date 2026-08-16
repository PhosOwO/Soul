from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Candidate:
    id: int | None
    scope_id: int
    type: str
    content: str
    status: str = "temp"
    source: str | None = None
    data: dict[str, Any] | None = None
