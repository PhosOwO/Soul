from __future__ import annotations

from soul.services.state_core.state_render import format_state_context
from soul.services.state_core.state_store import load_state


def build_context(limit: int = 10) -> str:
    return format_state_context(load_state(), limit=limit)
