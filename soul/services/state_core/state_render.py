from __future__ import annotations

from typing import Any

from soul.services.shared.constants import (
    PATCH_STATUS_ACCEPTED,
    STATE_KIND_ACCEPTED_BELIEF,
    STATE_KIND_ACTIVE_CONSTRAINT,
    STATE_KIND_DECISION_GATE,
    STATE_KIND_OPEN_QUESTION,
    STATE_KIND_TENTATIVE_OBSERVATION,
)
from soul.services.state_core.state_projection import project_state_items
from soul.services.shared.state_types import StateDoc, StateItem


def format_state_context(state: StateDoc, limit: int = 10, task: str = "") -> str:
    if state.get("current_state", {}).get("state_items"):
        return format_projected_state_context(state, task=task, limit=limit)

    current = state.get("current_state", {})
    beliefs = current.get("beliefs", [])[:limit]
    constraints = current.get("constraints", [])[:limit]
    questions = current.get("open_questions", [])[:limit]

    lines = [
        "Soul Current State",
        "",
        f"Project: {state.get('project', 'unknown')}",
        f"State version: {state.get('version', 0)}",
        f"Updated at: {state.get('updated_at', 'unknown')}",
        "",
        "Accepted Beliefs:",
    ]
    lines.extend(format_items(beliefs, "statement"))
    lines.extend(["", "Constraints:"])
    lines.extend(format_items(constraints))
    lines.extend(["", "Open Questions:"])
    lines.extend(format_items(questions))
    return "\n".join(lines)


def format_projected_state_context(state: StateDoc, task: str = "", limit: int = 10) -> str:
    projection = project_state_items(state, task=task, limit=limit)
    lines = [
        "Soul Current State",
        "",
        f"Project: {state.get('project', 'unknown')}",
        f"State version: {state.get('version', 0)}",
        f"Updated at: {state.get('updated_at', 'unknown')}",
        "Projection: task-relevant state items, not a planner or executor.",
        "",
    ]
    sections = [
        ("Active Constraints", STATE_KIND_ACTIVE_CONSTRAINT),
        ("Decision Gates", STATE_KIND_DECISION_GATE),
        ("Rejected Directions", "rejected_direction"),
        ("Working Hypotheses", "working_hypothesis"),
        ("Tentative Observations", STATE_KIND_TENTATIVE_OBSERVATION),
        ("Open Questions", STATE_KIND_OPEN_QUESTION),
        ("Accepted Beliefs", STATE_KIND_ACCEPTED_BELIEF),
    ]
    for title, kind in sections:
        items = [item for item in projection if item.get("kind") == kind]
        if not items:
            continue
        lines.extend([title + ":"])
        lines.extend(format_state_items(items))
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def format_state_items(items: list[StateItem]) -> list[str]:
    lines: list[str] = []
    for item in items:
        status = item.get("status", PATCH_STATUS_ACCEPTED)
        priority = item.get("priority", "medium")
        confidence = item.get("confidence", "unknown")
        lines.append(f"- [{status}, {priority}, confidence={confidence}] {item.get('statement', '')}")
    return lines


def format_items(items: list[Any], key: str | None = None) -> list[str]:
    if not items:
        return ["- none"]
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict) and key:
            status = item.get("status", "unknown")
            lines.append(f"- [{status}] {item.get(key, '')}")
        else:
            lines.append(f"- {item}")
    return lines
