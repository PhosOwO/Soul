from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from soul.services.state_policy import load_state_projection_policy
from soul.services.text import compact_text


DEFAULT_STATE_NAME = "state.json"
DEFAULT_STATE_MARKDOWN_NAME = "STATE.md"
DEFAULT_PATCH_LOG_NAME = "patch_proposals.jsonl"
@dataclass(frozen=True, slots=True)
class StatePaths:
    state_path: Path
    state_markdown_path: Path
    patch_log_path: Path


def brain_dir(project_dir: Path | None = None) -> Path:
    return (project_dir or Path.cwd()) / ".soul" / "state"


def state_paths(project_dir: Path | None = None) -> StatePaths:
    root = brain_dir(project_dir)
    return StatePaths(root / DEFAULT_STATE_NAME, root / DEFAULT_STATE_MARKDOWN_NAME, root / DEFAULT_PATCH_LOG_NAME)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def int_value(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return int(value)


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
    paths.state_markdown_path.write_text(format_state_context(state) + "\n", encoding="utf-8")


def load_state_markdown(project_dir: Path | None = None, limit: int = 10, task: str = "") -> str:
    state = load_state(project_dir)
    context = format_state_context(state, limit=limit, task=task)
    paths = state_paths(project_dir)
    if not paths.state_markdown_path.exists() or not task:
        paths.state_markdown_path.parent.mkdir(parents=True, exist_ok=True)
        paths.state_markdown_path.write_text(format_state_context(state) + "\n", encoding="utf-8")
    return context


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
    current_version = int_value(state.get("version"), 0)
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
    policy = load_state_projection_policy()
    relevance = 1 if state_item_matches_task(item, task) else 0
    priority = policy.priority_rank(str(item.get("priority", "medium")))
    kind = policy.kind_order(str(item.get("kind", "accepted_belief")))
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
    knowledge_points = knowledge_points_from_evidence(evidence)
    operations = explicit_operations_from_evidence(evidence)
    if not operations:
        operations = operations_from_knowledge_points(knowledge_points, evidence)

    title = str(evidence.get("title") or proposal_title_from_evidence(evidence, knowledge_points))
    why_remember = str(evidence.get("why_remember") or why_remember_from_evidence(evidence, knowledge_points))
    refs = proposal_refs_from_evidence(evidence)
    return {
        "id": f"patch-{uuid4().hex[:12]}",
        "created_at": utc_now(),
        "status": "proposed",
        "title": title,
        "knowledge_points": knowledge_points,
        "why_remember": why_remember,
        "refs": refs,
        "source": source,
        "base_version": state.get("version", 0),
        "evidence": evidence,
        "operations": operations,
        "review_recommendation": recommend_patch_review(operations),
    }


def knowledge_points_from_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = evidence.get("knowledge_points")
    if isinstance(explicit, list):
        points = [normalize_knowledge_point(point, evidence) for point in explicit if isinstance(point, dict)]
        return [point for point in points if point.get("statement")]

    state_item = evidence.get("state_item")
    state_items = evidence.get("state_items")
    candidates: list[dict[str, Any]] = []
    if isinstance(state_item, dict):
        candidates.append(state_item)
    if isinstance(state_items, list):
        candidates.extend(item for item in state_items if isinstance(item, dict))
    if candidates:
        return [normalize_knowledge_point(item, evidence) for item in candidates]

    text = str(evidence.get("summary") or evidence.get("content") or "")
    points: list[dict[str, Any]] = []
    for sentence in durable_sentences(text):
        point = normalize_knowledge_point(
            {
                "statement": sentence,
                "kind": infer_state_kind(sentence),
                "status": "needs_review",
                "priority": infer_priority(sentence),
                "confidence": 0.55,
                "why_remember": why_remember_for_statement(sentence),
                "ttl_turns": 8,
            },
            evidence,
        )
        points.append(point)
    return points


def normalize_knowledge_point(point: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    statement = compact_text(str(point.get("statement") or point.get("value") or ""), 260)
    if not statement:
        return {}
    return {
        "id": str(point.get("id") or stable_state_item_id(statement)),
        "kind": str(point.get("kind") or infer_state_kind(statement)),
        "statement": statement,
        "status": str(point.get("status") or "accepted"),
        "priority": str(point.get("priority") or infer_priority(statement)),
        "confidence": float(point.get("confidence", 0.75)),
        "why_remember": compact_text(
            str(point.get("why_remember") or evidence.get("why_remember") or why_remember_for_statement(statement)),
            240,
        ),
        **({"ttl_turns": point["ttl_turns"]} if isinstance(point.get("ttl_turns"), int) else {}),
    }


def durable_sentences(text: str) -> list[str]:
    normalized = " ".join(text.replace("\n", " ").split())
    if not normalized:
        return []
    raw_sentences = [
        sentence.strip(" -")
        for sentence in re.split(r"(?<=[.!?。！？])\s+|;\s+|；\s+", normalized)
        if sentence.strip(" -")
    ]
    durable: list[str] = []
    policy = load_state_projection_policy()
    for sentence in raw_sentences:
        lowered = sentence.lower()
        if policy.is_meta_review(lowered):
            continue
        if policy.is_episodic_prefix(lowered) and not contains_durable_signal(lowered):
            continue
        if contains_durable_signal(lowered):
            durable.append(compact_text(sentence, 260))
    return dedupe_preserve_order(durable)[:5]


def contains_durable_signal(lowered_sentence: str) -> bool:
    return load_state_projection_policy().contains_durable_signal(lowered_sentence)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def stable_state_item_id(statement: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", statement.lower()).strip("-")
    if not slug:
        slug = uuid4().hex[:8]
    return "knowledge-" + slug[:48].strip("-")


def infer_state_kind(statement: str) -> str:
    return load_state_projection_policy().infer_kind(statement)


def infer_priority(statement: str) -> str:
    return load_state_projection_policy().infer_priority(statement)


def why_remember_for_statement(statement: str) -> str:
    return load_state_projection_policy().why_remember(statement)


def proposal_title_from_evidence(evidence: dict[str, Any], knowledge_points: list[dict[str, Any]]) -> str:
    if knowledge_points:
        return compact_text(str(knowledge_points[0].get("statement", "State knowledge proposal")), 80)
    task = str(evidence.get("task") or evidence.get("summary") or evidence.get("source") or "State knowledge proposal")
    return compact_text(task, 80)


def why_remember_from_evidence(evidence: dict[str, Any], knowledge_points: list[dict[str, Any]]) -> str:
    if knowledge_points:
        return "Review these reusable knowledge points before they enter Soul Current State."
    if evidence.get("memory_owner") == "reme":
        return "ReMe retained the evidence, but Soul did not find durable project knowledge to project into state."
    return "No durable state knowledge was detected automatically."


def proposal_refs_from_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    if "evidence_refs" in evidence:
        refs["evidence_refs"] = evidence.get("evidence_refs")
    if "reme" in evidence:
        refs["reme"] = evidence.get("reme")
    raw_evidence = {
        key: value
        for key, value in evidence.items()
        if key not in {"evidence_refs", "reme", "operations", "state_item", "state_items", "knowledge_points"}
    }
    if raw_evidence:
        refs["raw_evidence"] = raw_evidence
    return refs


def operations_from_knowledge_points(knowledge_points: list[dict[str, Any]], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        add_state_item_operation(
            str(point["id"]),
            str(point["kind"]),
            str(point["statement"]),
            evidence,
            status=str(point.get("status", "accepted")),
            priority=str(point.get("priority", "medium")),
            confidence=float(point.get("confidence", 0.75)),
            ttl_turns=point.get("ttl_turns") if isinstance(point.get("ttl_turns"), int) else None,
            why_remember=str(point.get("why_remember", "")),
        )
        for point in knowledge_points
        if point.get("statement")
    ]


def explicit_operations_from_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    raw_operations = evidence.get("operations")
    if isinstance(raw_operations, list):
        return [dict(operation) for operation in raw_operations if isinstance(operation, dict)]

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
        why_remember=str(item.get("why_remember") or evidence.get("why_remember") or ""),
    )


def add_state_item_operation(
    item_id: str,
    kind: str,
    statement: str,
    evidence: dict[str, Any],
    status: str = "accepted",
    priority: str = "medium",
    confidence: float = 0.75,
    ttl_turns: int | None = None,
    why_remember: str = "",
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
    if why_remember:
        value["why_remember"] = why_remember
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
    next_version = int_value(new_state.get("version"), 0) + 1
    operations = operations_for_apply(proposal)

    for operation in operations:
        op = operation.get("op")
        if op == "upsert_belief":
            value = dict(operation["value"])
            value["updated_at"] = now
            existing = next((belief for belief in beliefs if belief.get("id") == value["id"]), None)
            if existing:
                value["evidence_count"] = int_value(existing.get("evidence_count"), 0) + 1
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
                value["evidence_count"] = int_value(existing.get("evidence_count"), 0) + 1
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
            "operations": operations,
        }
    )
    return new_state


def operations_for_apply(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    knowledge_points = proposal.get("knowledge_points")
    if isinstance(knowledge_points, list) and knowledge_points:
        raw_evidence = proposal.get("evidence")
        evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
        return operations_from_knowledge_points(
            [point for point in knowledge_points if isinstance(point, dict)],
            evidence,
        )
    return [operation for operation in proposal.get("operations", []) if isinstance(operation, dict)]


def append_patch_status(
    proposal: dict[str, Any],
    status: str,
    project_dir: Path | None = None,
    *,
    reason: str = "",
    updated_by: str = "user",
) -> dict[str, Any]:
    record = json.loads(json.dumps(proposal, ensure_ascii=False))
    record["status"] = status
    record["updated_at"] = utc_now()
    record["updated_by"] = updated_by
    if reason:
        record["status_reason"] = reason
    append_patch_proposal(record, project_dir)
    return record


def edit_patch_proposal(
    proposal: dict[str, Any],
    project_dir: Path | None = None,
    *,
    title: str | None = None,
    why_remember: str | None = None,
    knowledge_points: list[dict[str, Any]] | None = None,
    updated_by: str = "user",
) -> dict[str, Any]:
    edited = json.loads(json.dumps(proposal, ensure_ascii=False))
    if title is not None:
        edited["title"] = title
    if why_remember is not None:
        edited["why_remember"] = why_remember
    if knowledge_points is not None:
        evidence = edited.get("evidence") if isinstance(edited.get("evidence"), dict) else {}
        edited["knowledge_points"] = [normalize_knowledge_point(point, evidence) for point in knowledge_points]
        edited["operations"] = operations_from_knowledge_points(edited["knowledge_points"], evidence)
        edited["review_recommendation"] = recommend_patch_review(edited["operations"])
    edited["status"] = "proposed"
    edited["updated_at"] = utc_now()
    edited["updated_by"] = updated_by
    edited["revision_of"] = proposal.get("id")
    append_patch_proposal(edited, project_dir)
    return edited


