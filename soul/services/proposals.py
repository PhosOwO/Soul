from __future__ import annotations

import json
from typing import Any, Mapping, Sequence, cast
from uuid import uuid4

from soul.services.knowledge import knowledge_points_from_evidence, normalize_knowledge_point
from soul.services.constants import (
    MEMORY_OWNER_REME,
    PATCH_OP_ADD_CONSTRAINT,
    PATCH_OP_ADD_OPEN_QUESTION,
    PATCH_OP_UPSERT_BELIEF,
    PATCH_OP_UPSERT_STATE_ITEM,
    PATCH_REVIEW_AUTO_ACCEPT,
    PATCH_REVIEW_NEEDS_REVIEW,
    PATCH_REVIEW_REJECT,
    PATCH_STATUS_ACCEPTED,
    PATCH_STATUS_NEEDS_REVIEW,
    PATCH_STATUS_PROPOSED,
    PATCH_STATUS_TENTATIVE,
    STATE_KIND_ACCEPTED_BELIEF,
    STATE_KIND_OPEN_QUESTION,
    STATE_KIND_TENTATIVE_OBSERVATION,
)
from soul.services.state_store import append_patch_proposal, int_value, utc_now
from soul.services.state_types import KnowledgePoint, PatchOperation, PatchProposal, ProposalRefs, StateDoc, StateItem
from soul.services.text import compact_text


def propose_patch(
    state: StateDoc,
    evidence: dict[str, Any],
    source: str = "soul state diff",
) -> PatchProposal:
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
        "status": PATCH_STATUS_PROPOSED,
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


def proposal_title_from_evidence(evidence: dict[str, Any], knowledge_points: list[KnowledgePoint]) -> str:
    if knowledge_points:
        return compact_text(str(knowledge_points[0].get("statement", "State knowledge proposal")), 80)
    task = str(evidence.get("task") or evidence.get("summary") or evidence.get("source") or "State knowledge proposal")
    return compact_text(task, 80)


def why_remember_from_evidence(evidence: dict[str, Any], knowledge_points: list[KnowledgePoint]) -> str:
    if knowledge_points:
        return "Review these reusable knowledge points before they enter Soul Current State."
    if evidence.get("memory_owner") == MEMORY_OWNER_REME:
        return "ReMe retained the evidence, but Soul did not find durable project knowledge to project into state."
    return "No durable state knowledge was detected automatically."


def proposal_refs_from_evidence(evidence: dict[str, Any]) -> ProposalRefs:
    refs: ProposalRefs = {}
    evidence_refs = evidence.get("evidence_refs")
    if isinstance(evidence_refs, list):
        refs["evidence_refs"] = [ref for ref in evidence_refs if isinstance(ref, dict)]
    reme = evidence.get("reme")
    if isinstance(reme, dict):
        refs["reme"] = reme
    raw_evidence = {
        key: value
        for key, value in evidence.items()
        if key not in {"evidence_refs", "reme", "operations", "state_item", "state_items", "knowledge_points"}
    }
    if raw_evidence:
        refs["raw_evidence"] = raw_evidence
    return refs


def operations_from_knowledge_points(knowledge_points: list[KnowledgePoint], evidence: dict[str, Any]) -> list[PatchOperation]:
    return [
        add_state_item_operation(
            str(point.get("id", "")),
            str(point.get("kind", STATE_KIND_ACCEPTED_BELIEF)),
            str(point.get("statement", "")),
            evidence,
            status=str(point.get("status", PATCH_STATUS_ACCEPTED)),
            priority=str(point.get("priority", "medium")),
            confidence=float(point.get("confidence", 0.75)),
            ttl_turns=point.get("ttl_turns") if isinstance(point.get("ttl_turns"), int) else None,
            why_remember=str(point.get("why_remember", "")),
        )
        for point in knowledge_points
        if point.get("statement")
    ]


def explicit_operations_from_evidence(evidence: dict[str, Any]) -> list[PatchOperation]:
    raw_operations = evidence.get("operations")
    if isinstance(raw_operations, list):
        return cast(list[PatchOperation], [dict(operation) for operation in raw_operations if isinstance(operation, dict)])

    state_item = evidence.get("state_item")
    state_items = evidence.get("state_items")
    candidates: list[Any] = []
    if isinstance(state_item, dict):
        candidates.append(state_item)
    if isinstance(state_items, list):
        candidates.extend(item for item in state_items if isinstance(item, dict))

    operations: list[PatchOperation] = []
    for item in candidates:
        operations.append(state_item_operation_from_payload(item, evidence))
    return operations


def state_item_operation_from_payload(item: StateItem, evidence: dict[str, Any]) -> PatchOperation:
    item_id = str(item.get("id") or f"state-item-{uuid4().hex[:8]}")
    kind = str(item.get("kind") or STATE_KIND_OPEN_QUESTION)
    statement = str(item.get("statement") or item.get("value") or "")
    if not statement.strip():
        raise ValueError("state item proposal requires a non-empty statement")
    return add_state_item_operation(
        item_id,
        kind,
        statement,
        evidence,
        status=str(item.get("status", PATCH_STATUS_ACCEPTED)),
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
    status: str = PATCH_STATUS_ACCEPTED,
    priority: str = "medium",
    confidence: float = 0.75,
    ttl_turns: int | None = None,
    why_remember: str = "",
) -> PatchOperation:
    value: StateItem = {
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
    return {"op": PATCH_OP_UPSERT_STATE_ITEM, "id": item_id, "value": value, "evidence": evidence}


def recommend_patch_review(operations: list[PatchOperation]) -> str:
    if not operations:
        return PATCH_REVIEW_REJECT
    statuses = [
        str(operation.get("value", {}).get("status", PATCH_STATUS_ACCEPTED))
        for operation in operations
        if isinstance(operation.get("value"), dict)
    ]
    kinds = [
        str(operation.get("value", {}).get("kind", ""))
        for operation in operations
        if isinstance(operation.get("value"), dict)
    ]
    if any(status in {PATCH_STATUS_NEEDS_REVIEW, PATCH_STATUS_TENTATIVE} for status in statuses):
        return PATCH_REVIEW_NEEDS_REVIEW
    if any(kind in {STATE_KIND_OPEN_QUESTION, STATE_KIND_TENTATIVE_OBSERVATION} for kind in kinds):
        return PATCH_REVIEW_NEEDS_REVIEW
    return PATCH_REVIEW_AUTO_ACCEPT


def apply_patch_proposal(
    state: StateDoc,
    proposal: PatchProposal,
    confirmed_by: str = "user",
) -> StateDoc:
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
        if op == PATCH_OP_UPSERT_BELIEF:
            value = dict(operation["value"])
            value["updated_at"] = now
            existing = next((belief for belief in beliefs if belief.get("id") == value["id"]), None)
            if existing:
                value["evidence_count"] = int_value(existing.get("evidence_count"), 0) + 1
                existing.update(value)
            else:
                beliefs.append(value)
        elif op == PATCH_OP_ADD_CONSTRAINT:
            value = operation.get("value")
            if value and value not in constraints:
                constraints.append(value)
        elif op == PATCH_OP_ADD_OPEN_QUESTION:
            value = operation.get("value")
            if value and value not in questions:
                questions.append(value)
        elif op == PATCH_OP_UPSERT_STATE_ITEM:
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


def operations_for_apply(proposal: PatchProposal) -> list[PatchOperation]:
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
    proposal: PatchProposal,
    status: str,
    project_dir: Any = None,
    *,
    reason: str = "",
    updated_by: str = "user",
) -> PatchProposal:
    record = json.loads(json.dumps(proposal, ensure_ascii=False))
    record["status"] = status
    record["updated_at"] = utc_now()
    record["updated_by"] = updated_by
    if reason:
        record["status_reason"] = reason
    append_patch_proposal(record, project_dir)
    return record


def edit_patch_proposal(
    proposal: PatchProposal,
    project_dir: Any = None,
    *,
    title: str | None = None,
    why_remember: str | None = None,
    knowledge_points: Sequence[Mapping[str, Any]] | None = None,
    updated_by: str = "user",
) -> PatchProposal:
    edited = json.loads(json.dumps(proposal, ensure_ascii=False))
    if title is not None:
        edited["title"] = title
    if why_remember is not None:
        edited["why_remember"] = why_remember
    if knowledge_points is not None:
        raw_evidence = edited.get("evidence")
        evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
        edited["knowledge_points"] = [normalize_knowledge_point(point, evidence) for point in knowledge_points]
        edited["operations"] = operations_from_knowledge_points(edited["knowledge_points"], evidence)
        edited["review_recommendation"] = recommend_patch_review(edited["operations"])
    edited["status"] = PATCH_STATUS_PROPOSED
    edited["updated_at"] = utc_now()
    edited["updated_by"] = updated_by
    edited["revision_of"] = proposal.get("id")
    append_patch_proposal(edited, project_dir)
    return edited
