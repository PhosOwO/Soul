from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Artifact:
    id: int | None
    scope_id: int
    path: str
    kind: str = "file"
    source_episode_id: int | None = None
    data: dict[str, Any] | None = None
