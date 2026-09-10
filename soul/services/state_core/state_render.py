from __future__ import annotations

import hashlib
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

DEFAULT_SEED_STATE_IDS = {"state-centric-loop", "patch-confirm-gate"}
DEFAULT_SEED_STATE_STATEMENTS = {
    (
        "Soul's core loop is Accepted State plus Working State: evidence can update short-lived Working State "
        "immediately, while accepted state changes require explicit State Patch confirmation."
    ),
    "Episodes and system logs are evidence, not accepted cognition.",
    "State patches require explicit confirmation before changing accepted state.",
    "Entity and Candidate promotion are not part of the active Soul loop.",
}
INJECTABLE_ACCEPTED_KINDS = {
    STATE_KIND_ACTIVE_CONSTRAINT,
    STATE_KIND_DECISION_GATE,
    "rejected_direction",
    STATE_KIND_ACCEPTED_BELIEF,
}


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


def format_accepted_state_injection(state: StateDoc, limit: int = 10, task: str = "") -> str:
    items = accepted_state_items_for_injection(state, limit=limit, task=task)
    if not items:
        return ""

    lines = [
        "Soul Accepted State",
        "",
        "Use these entries as confirmed project memory. They are not a plan or task instructions.",
        "If an entry conflicts with a current system, developer, or user instruction, follow the current higher-priority instruction and surface the conflict briefly.",
        "",
        f"Project: {state.get('project', 'unknown')}",
        f"State version: {state.get('version', 0)}",
        f"Updated at: {state.get('updated_at', 'unknown')}",
        "",
    ]
    sections = [
        ("Active Constraints", STATE_KIND_ACTIVE_CONSTRAINT),
        ("Decision Gates", STATE_KIND_DECISION_GATE),
        ("Rejected Directions", "rejected_direction"),
        ("Accepted Beliefs", STATE_KIND_ACCEPTED_BELIEF),
    ]
    for title, kind in sections:
        section_items = [item for item in items if item.get("kind", STATE_KIND_ACCEPTED_BELIEF) == kind]
        if not section_items:
            continue
        lines.append(title + ":")
        lines.extend(format_state_items(section_items))
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def accepted_state_items_for_injection(state: StateDoc, limit: int = 10, task: str = "") -> list[StateItem]:
    current = state.get("current_state", {})
    state_items = current.get("state_items", [])
    if state_items:
        return [
            item
            for item in project_state_items(state, task=task, limit=limit * 3)
            if is_injectable_accepted_state_item(item)
        ][:limit]

    items: list[StateItem] = []
    for constraint in current.get("constraints", []):
        statement = constraint if isinstance(constraint, str) else constraint.get("statement", "")
        if statement and not is_default_seed_statement(statement):
            items.append(legacy_state_item(STATE_KIND_ACTIVE_CONSTRAINT, statement, priority="high"))
    for belief in current.get("beliefs", []):
        if belief.get("status", PATCH_STATUS_ACCEPTED) != PATCH_STATUS_ACCEPTED:
            continue
        statement = belief.get("statement", "")
        if statement and not is_default_seed_statement(statement):
            items.append(
                legacy_state_item(
                    STATE_KIND_ACCEPTED_BELIEF,
                    statement,
                    item_id=belief.get("id"),
                    status=PATCH_STATUS_ACCEPTED,
                    priority=belief.get("priority"),
                    confidence=belief.get("confidence"),
                    evidence_count=belief.get("evidence_count"),
                    latest_evidence=belief.get("latest_evidence"),
                    why_remember=belief.get("why_remember"),
                )
            )
    return items[:limit]


def legacy_state_item(
    kind: str,
    statement: str,
    *,
    item_id: Any = None,
    status: str = PATCH_STATUS_ACCEPTED,
    priority: Any = None,
    confidence: Any = None,
    evidence_count: Any = None,
    latest_evidence: Any = None,
    why_remember: Any = None,
) -> StateItem:
    item: StateItem = {
        "id": str(item_id) if item_id else legacy_state_item_id(kind, statement),
        "kind": kind,
        "statement": statement,
        "status": status,
        "priority": str(priority or "medium"),
    }
    if isinstance(confidence, (int, float)):
        item["confidence"] = float(confidence)
    if isinstance(evidence_count, int):
        item["evidence_count"] = evidence_count
    if latest_evidence:
        item["latest_evidence"] = str(latest_evidence)
    if why_remember:
        item["why_remember"] = str(why_remember)
    return item


def legacy_state_item_id(kind: str, statement: str) -> str:
    digest = hashlib.sha1(f"{kind}\0{statement}".encode("utf-8")).hexdigest()[:12]
    return f"legacy-{kind}-{digest}"


def is_injectable_accepted_state_item(item: StateItem) -> bool:
    if item.get("status", PATCH_STATUS_ACCEPTED) != PATCH_STATUS_ACCEPTED:
        return False
    if item.get("kind", STATE_KIND_ACCEPTED_BELIEF) not in INJECTABLE_ACCEPTED_KINDS:
        return False
    if item.get("id") in DEFAULT_SEED_STATE_IDS:
        return False
    return not is_default_seed_statement(str(item.get("statement", "")))


def is_default_seed_statement(statement: str) -> bool:
    return statement.strip() in DEFAULT_SEED_STATE_STATEMENTS


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
