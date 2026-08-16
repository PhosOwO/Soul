from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from soul.storage.database import dumps_json


DEFAULT_STATE_NAME = "state.json"
DEFAULT_PATCH_LOG_NAME = "patch_proposals.jsonl"
STATE_KIND_ORDER = {
    "active_constraint": 0,
    "decision_gate": 1,
    "rejected_direction": 2,
    "working_hypothesis": 3,
    "tentative_observation": 4,
    "open_question": 5,
    "accepted_belief": 6,
}
PRIORITY_SCORE = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True, slots=True)
class StatePaths:
    state_path: Path
    patch_log_path: Path


def brain_dir(project_dir: Path | None = None) -> Path:
    return (project_dir or Path.cwd()) / ".soul" / "state"


def state_paths(project_dir: Path | None = None) -> StatePaths:
    root = brain_dir(project_dir)
    return StatePaths(root / DEFAULT_STATE_NAME, root / DEFAULT_PATCH_LOG_NAME)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def initial_state(project_name: str = "Soul Project") -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": 1,
        "project": project_name,
        "version": 1,
        "updated_at": now,
        "current_state": {
            "beliefs": [
                {
                    "id": "state-centric-loop",
                    "statement": (
                        "Soul's core loop is Current State -> Evidence -> Cognitive Diff "
                        "-> State Patch -> Confirm -> New State."
                    ),
                    "status": "accepted",
                    "confidence": 0.9,
                    "evidence_count": 1,
                    "latest_evidence": "Initial Soul design correction.",
                    "updated_at": now,
                }
            ],
            "constraints": [
                "Episodes and system logs are evidence, not accepted cognition.",
                "State patches require explicit confirmation before changing accepted state.",
                "Entity and Candidate promotion are not part of the active Soul loop.",
            ],
            "open_questions": [],
            "state_items": [
                {
                    "id": "state-centric-loop",
                    "kind": "accepted_belief",
                    "statement": (
                        "Soul's core loop is Current State -> Evidence -> Cognitive Diff "
                        "-> State Patch -> Confirm -> New State."
                    ),
                    "status": "accepted",
                    "priority": "high",
                    "confidence": 0.9,
                    "evidence_count": 1,
                    "latest_evidence": "Initial Soul design correction.",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "patch-confirm-gate",
                    "kind": "active_constraint",
                    "statement": "State patches require explicit confirmation before changing accepted state.",
                    "status": "accepted",
                    "priority": "high",
                    "confidence": 0.9,
                    "evidence_count": 1,
                    "latest_evidence": "Initial Soul design correction.",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        },
        "history": [],
    }


def load_state(project_dir: Path | None = None, project_name: str = "Soul Project") -> dict[str, Any]:
    paths = state_paths(project_dir)
    if not paths.state_path.exists():
        save_state(initial_state(project_name), project_dir)
    return json.loads(paths.state_path.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any], project_dir: Path | None = None) -> None:
    paths = state_paths(project_dir)
    paths.state_path.parent.mkdir(parents=True, exist_ok=True)
    paths.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_state_context(state: dict[str, Any], limit: int = 10, task: str = "") -> str:
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


def format_projected_state_context(state: dict[str, Any], task: str = "", limit: int = 10) -> str:
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
        ("Active Constraints", "active_constraint"),
        ("Decision Gates", "decision_gate"),
        ("Rejected Directions", "rejected_direction"),
        ("Working Hypotheses", "working_hypothesis"),
        ("Tentative Observations", "tentative_observation"),
        ("Open Questions", "open_question"),
        ("Accepted Beliefs", "accepted_belief"),
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


def project_state_items(state: dict[str, Any], task: str = "", limit: int = 10) -> list[dict[str, Any]]:
    items = list(state.get("current_state", {}).get("state_items", []))
    current_version = int(state.get("version", 0))
    active = [item for item in items if is_state_item_active(item, current_version)]
    active.sort(key=lambda item: projection_sort_key(item, task))
    return active[:limit]


def is_state_item_active(item: dict[str, Any], current_version: int) -> bool:
    status = item.get("status", "accepted")
    if status in {"rejected", "archived"}:
        return False
    ttl_turns = item.get("ttl_turns")
    created_version = item.get("created_version")
    if isinstance(ttl_turns, int) and isinstance(created_version, int):
        return current_version - created_version <= ttl_turns
    return True


def projection_sort_key(item: dict[str, Any], task: str) -> tuple[int, int, int, str]:
    relevance = 1 if state_item_matches_task(item, task) else 0
    priority = PRIORITY_SCORE.get(str(item.get("priority", "medium")), 2)
    kind = STATE_KIND_ORDER.get(str(item.get("kind", "accepted_belief")), 99)
    return (-priority, -relevance, kind, str(item.get("id", "")))


def state_item_matches_task(item: dict[str, Any], task: str) -> bool:
    if not task.strip():
        return False
    haystack = " ".join(str(item.get(key, "")) for key in ("id", "kind", "statement")).lower()
    tokens = [token.lower() for token in task.replace("/", " ").replace("_", " ").split() if len(token) >= 3]
    return any(token in haystack for token in tokens)


def format_state_items(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        status = item.get("status", "accepted")
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


def propose_patch(
    state: dict[str, Any],
    evidence: dict[str, Any],
    source: str = "soul state diff",
) -> dict[str, Any]:
    text = " ".join(str(part) for part in [evidence.get("summary"), evidence.get("content")] if part)
    operations = explicit_operations_from_evidence(evidence)
    if not operations and text.strip():
        operations.append(
            add_state_item_operation(
                "review-evidence-" + uuid4().hex[:8],
                "open_question",
                f"Review whether this evidence changes Soul state: {compact_text(text, 160)}",
                evidence,
                status="needs_review",
                priority="low",
                confidence=0.4,
                ttl_turns=6,
            )
        )

    return {
        "id": f"patch-{uuid4().hex[:12]}",
        "created_at": utc_now(),
        "status": "proposed",
        "source": source,
        "base_version": state.get("version", 0),
        "evidence": evidence,
        "operations": operations,
        "review_recommendation": recommend_patch_review(operations),
    }


def explicit_operations_from_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    operations = evidence.get("operations")
    if isinstance(operations, list):
        return [dict(operation) for operation in operations if isinstance(operation, dict)]

    state_item = evidence.get("state_item")
    state_items = evidence.get("state_items")
    candidates: list[Any] = []
    if isinstance(state_item, dict):
        candidates.append(state_item)
    if isinstance(state_items, list):
        candidates.extend(item for item in state_items if isinstance(item, dict))

    operations: list[dict[str, Any]] = []
    for item in candidates:
        operations.append(state_item_operation_from_payload(item, evidence))
    return operations


def state_item_operation_from_payload(item: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    item_id = str(item.get("id") or f"state-item-{uuid4().hex[:8]}")
    kind = str(item.get("kind") or "open_question")
    statement = str(item.get("statement") or item.get("value") or "")
    if not statement.strip():
        raise ValueError("state item proposal requires a non-empty statement")
    return add_state_item_operation(
        item_id,
        kind,
        statement,
        evidence,
        status=str(item.get("status", "accepted")),
        priority=str(item.get("priority", "medium")),
        confidence=float(item.get("confidence", 0.75)),
        ttl_turns=item.get("ttl_turns") if isinstance(item.get("ttl_turns"), int) else None,
    )


def upsert_belief_operation(belief_id: str, statement: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "upsert_belief",
        "id": belief_id,
        "value": {
            "id": belief_id,
            "statement": statement,
            "status": "accepted",
            "confidence": 0.9,
            "evidence_count": 1,
            "latest_evidence": evidence.get("summary") or evidence.get("source") or "manual evidence",
        },
        "evidence": evidence,
    }


def add_state_item_operation(
    item_id: str,
    kind: str,
    statement: str,
    evidence: dict[str, Any],
    status: str = "accepted",
    priority: str = "medium",
    confidence: float = 0.75,
    ttl_turns: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": item_id,
        "kind": kind,
        "statement": statement,
        "status": status,
        "priority": priority,
        "confidence": confidence,
        "evidence_count": 1,
        "latest_evidence": evidence.get("summary") or evidence.get("source") or "manual evidence",
    }
    if ttl_turns is not None:
        value["ttl_turns"] = ttl_turns
    return {"op": "upsert_state_item", "id": item_id, "value": value, "evidence": evidence}


def recommend_patch_review(operations: list[dict[str, Any]]) -> str:
    if not operations:
        return "reject"
    statuses = [
        str(operation.get("value", {}).get("status", "accepted"))
        for operation in operations
        if isinstance(operation.get("value"), dict)
    ]
    kinds = [
        str(operation.get("value", {}).get("kind", ""))
        for operation in operations
        if isinstance(operation.get("value"), dict)
    ]
    if any(status in {"needs_review", "tentative"} for status in statuses):
        return "needs_review"
    if any(kind in {"open_question", "tentative_observation"} for kind in kinds):
        return "needs_review"
    return "auto_accept"


def add_constraint_operation(statement: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"op": "add_constraint", "value": statement, "evidence": evidence}


def append_patch_proposal(proposal: dict[str, Any], project_dir: Path | None = None) -> None:
    paths = state_paths(project_dir)
    paths.patch_log_path.parent.mkdir(parents=True, exist_ok=True)
    with paths.patch_log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(proposal, ensure_ascii=False) + "\n")


def load_patch_proposals(project_dir: Path | None = None) -> list[dict[str, Any]]:
    paths = state_paths(project_dir)
    if not paths.patch_log_path.exists():
        return []
    return [
        json.loads(line)
        for line in paths.patch_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def find_patch_proposal(proposal_id: str, project_dir: Path | None = None) -> dict[str, Any]:
    for proposal in reversed(load_patch_proposals(project_dir)):
        if proposal.get("id") == proposal_id:
            return proposal
    raise ValueError(f"Patch proposal not found: {proposal_id}")


def apply_patch_proposal(
    state: dict[str, Any],
    proposal: dict[str, Any],
    confirmed_by: str = "user",
) -> dict[str, Any]:
    new_state = json.loads(json.dumps(state, ensure_ascii=False))
    current = new_state.setdefault("current_state", {})
    beliefs = current.setdefault("beliefs", [])
    constraints = current.setdefault("constraints", [])
    questions = current.setdefault("open_questions", [])
    state_items = current.setdefault("state_items", [])
    now = utc_now()
    next_version = int(new_state.get("version", 0)) + 1

    for operation in proposal.get("operations", []):
        op = operation.get("op")
        if op == "upsert_belief":
            value = dict(operation["value"])
            value["updated_at"] = now
            existing = next((belief for belief in beliefs if belief.get("id") == value["id"]), None)
            if existing:
                value["evidence_count"] = int(existing.get("evidence_count", 0)) + 1
                existing.update(value)
            else:
                beliefs.append(value)
        elif op == "add_constraint":
            value = operation.get("value")
            if value and value not in constraints:
                constraints.append(value)
        elif op == "add_open_question":
            value = operation.get("value")
            if value and value not in questions:
                questions.append(value)
        elif op == "upsert_state_item":
            value = dict(operation["value"])
            value["updated_at"] = now
            value.setdefault("created_at", now)
            value.setdefault("created_version", next_version)
            existing = next((item for item in state_items if item.get("id") == value["id"]), None)
            if existing:
                value["evidence_count"] = int(existing.get("evidence_count", 0)) + 1
                value.setdefault("created_at", existing.get("created_at", now))
                value.setdefault("created_version", existing.get("created_version", next_version))
                existing.update(value)
            else:
                state_items.append(value)

    new_state["version"] = next_version
    new_state["updated_at"] = now
    new_state.setdefault("history", []).append(
        {
            "proposal_id": proposal.get("id"),
            "confirmed_by": confirmed_by,
            "applied_at": now,
            "operations": proposal.get("operations", []),
        }
    )
    return new_state


def record_state_event(
    conn: sqlite3.Connection,
    event_type: str,
    proposal: dict[str, Any],
    source: str,
    reason: str,
) -> None:
    conn.execute(
        """
        INSERT INTO cognitive_events (type, change_json, reason, source)
        VALUES (?, ?, ?, ?)
        """,
        (event_type, dumps_json(proposal), reason, source),
    )


def compact_text(text: str, max_length: int) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= max_length:
        return one_line
    return one_line[: max_length - 3].rstrip() + "..."
