from __future__ import annotations

from soul.services.state_core.state_policy import load_state_projection_policy
from soul.services.state_core.state_store import int_value
from soul.services.shared.state_types import StateDoc, StateItem
from soul.services.shared.constants import PATCH_STATUS_ACCEPTED, PATCH_STATUS_ARCHIVED, PATCH_STATUS_REJECTED, STATE_KIND_ACCEPTED_BELIEF


def project_state_items(state: StateDoc, task: str = "", limit: int = 10) -> list[StateItem]:
    items = list(state.get("current_state", {}).get("state_items", []))
    current_version = int_value(state.get("version"), 0)
    active = [item for item in items if is_state_item_active(item, current_version)]
    active.sort(key=lambda item: projection_sort_key(item, task))
    return active[:limit]


def is_state_item_active(item: StateItem, current_version: int) -> bool:
    status = item.get("status", PATCH_STATUS_ACCEPTED)
    if status in {PATCH_STATUS_REJECTED, PATCH_STATUS_ARCHIVED}:
        return False
    ttl_turns = item.get("ttl_turns")
    created_version = item.get("created_version")
    if isinstance(ttl_turns, int) and isinstance(created_version, int):
        return current_version - created_version <= ttl_turns
    return True


def projection_sort_key(item: StateItem, task: str) -> tuple[int, int, int, str]:
    policy = load_state_projection_policy()
    relevance = 1 if state_item_matches_task(item, task) else 0
    priority = policy.priority_rank(str(item.get("priority", "medium")))
    kind = policy.kind_order(str(item.get("kind", STATE_KIND_ACCEPTED_BELIEF)))
    return (-priority, -relevance, kind, str(item.get("id", "")))


def state_item_matches_task(item: StateItem, task: str) -> bool:
    if not task.strip():
        return False
    haystack = " ".join(str(item.get(key, "")) for key in ("id", "kind", "statement")).lower()
    tokens = [token.lower() for token in task.replace("/", " ").replace("_", " ").split() if len(token) >= 3]
    return any(token in haystack for token in tokens)
