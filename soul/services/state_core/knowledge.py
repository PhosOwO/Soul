from __future__ import annotations

import re
from typing import Any, Mapping, cast
from uuid import uuid4

from soul.services.shared.constants import PATCH_STATUS_ACCEPTED, PATCH_STATUS_NEEDS_REVIEW
from soul.services.state_core.state_policy import load_state_projection_policy
from soul.services.shared.state_types import KnowledgePoint, StateItem
from soul.services.shared.text import compact_text


def knowledge_points_from_evidence(evidence: dict[str, Any]) -> list[KnowledgePoint]:
    explicit = evidence.get("knowledge_points")
    if isinstance(explicit, list):
        points: list[KnowledgePoint] = []
        for point in explicit:
            if not isinstance(point, dict):
                continue
            normalized = normalize_knowledge_point(point, evidence)
            if normalized is not None:
                points.append(normalized)
        return points

    state_item = evidence.get("state_item")
    state_items = evidence.get("state_items")
    candidates: list[StateItem] = []
    if isinstance(state_item, dict):
        candidates.append(cast(StateItem, state_item))
    if isinstance(state_items, list):
        candidates.extend(cast(StateItem, item) for item in state_items if isinstance(item, dict))
    if candidates:
        return [
            point
            for point in (normalize_knowledge_point(item, evidence) for item in candidates)
            if point is not None
        ]

    text = str(evidence.get("summary") or evidence.get("content") or "")
    points: list[KnowledgePoint] = []
    for sentence in durable_sentences(text):
        point = normalize_knowledge_point(
            {
                "statement": sentence,
                "kind": infer_state_kind(sentence),
                "status": PATCH_STATUS_NEEDS_REVIEW,
                "priority": infer_priority(sentence),
                "confidence": 0.55,
                "why_remember": why_remember_for_statement(sentence),
                "ttl_turns": 8,
            },
            evidence,
        )
        if point is not None:
            points.append(point)
    return points


def normalize_knowledge_point(point: Mapping[str, Any], evidence: dict[str, Any]) -> KnowledgePoint | None:
    statement = compact_text(str(point.get("statement") or point.get("value") or ""), 260)
    if not statement:
        return None
    return cast(KnowledgePoint, {
        "id": str(point.get("id") or stable_state_item_id(statement)),
        "kind": str(point.get("kind") or infer_state_kind(statement)),
        "statement": statement,
        "status": str(point.get("status") or PATCH_STATUS_ACCEPTED),
        "priority": str(point.get("priority") or infer_priority(statement)),
        "confidence": float(point.get("confidence", 0.75)),
        "why_remember": compact_text(
            str(point.get("why_remember") or evidence.get("why_remember") or why_remember_for_statement(statement)),
            240,
        ),
        **({"ttl_turns": point["ttl_turns"]} if isinstance(point.get("ttl_turns"), int) else {}),
    })


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
