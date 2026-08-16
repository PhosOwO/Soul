from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Episode:
    id: int | None
    scope_id: int
    source: str
    summary: str
    content: list[dict[str, Any]]
    source_path: str | None = None
    metadata: dict[str, Any] | None = None
