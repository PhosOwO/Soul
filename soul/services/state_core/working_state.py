from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from soul.services.shared.constants import (
    PATCH_STATUS_NEEDS_REVIEW,
    STATE_KIND_ACCEPTED_BELIEF,
    STATE_KIND_ACTIVE_CONSTRAINT,
    STATE_KIND_OPEN_QUESTION,
    WORKING_STATUS_CONFLICT_NEEDS_REVIEW,
    WORKING_STATUS_EXPIRED,
    WORKING_STATUS_PROMOTED,
    WORKING_STATUS_REJECTED,
    WORKING_STATUS_WORKING,
)
from soul.services.shared.state_types import StateDoc, WorkingStateDoc, WorkingStateItem
from soul.services.shared.text import compact_text
from soul.services.state_core.knowledge import (
    infer_priority,
    infer_state_kind,
    stable_state_item_id,
    why_remember_for_statement,
)
from soul.services.state_core.proposals import propose_patch
from soul.services.state_core.state_projection import state_item_matches_task
from soul.services.state_core.state_store import append_patch_proposal, brain_dir, load_state, utc_now
from soul.services.state_core.working_state_policy import (
    DEFAULT_WORKING_EXPIRY,
    DEFAULT_WORKING_REVIEW_AFTER,
    CONFLICT_NEGATION_SIGNALS,
    WORKING_REASON_MAX_LENGTH,
    WORKING_SCOPE_MAX_LENGTH,
    WORKING_STATEMENT_MAX_LENGTH,
    WORKING_TOKEN_MIN_LENGTH,
)


DEFAULT_WORKING_EVENTS_NAME = "working_events.jsonl"
DEFAULT_WORKING_STATE_NAME = "working_state.json"
WORKING_KIND_PROJECT_FACT = "project_fact"
WORKING_KIND_CONSTRAINT = "constraint"
WORKING_KIND_PREFERENCE = "preference"
WORKING_KIND_VERIFICATION_RESULT = "verification_result"
WORKING_KIND_OPEN_QUESTION = "open_question"
WORKING_STATE_KINDS = {
    WORKING_KIND_PROJECT_FACT,
    WORKING_KIND_CONSTRAINT,
    WORKING_KIND_PREFERENCE,
    WORKING_KIND_VERIFICATION_RESULT,
    WORKING_KIND_OPEN_QUESTION,
}
WORKING_KIND_TO_STATE_KIND = {
    WORKING_KIND_PROJECT_FACT: STATE_KIND_ACCEPTED_BELIEF,
    WORKING_KIND_CONSTRAINT: STATE_KIND_ACTIVE_CONSTRAINT,
    WORKING_KIND_PREFERENCE: STATE_KIND_ACCEPTED_BELIEF,
    WORKING_KIND_VERIFICATION_RESULT: STATE_KIND_ACCEPTED_BELIEF,
    WORKING_KIND_OPEN_QUESTION: STATE_KIND_OPEN_QUESTION,
}
WORKING_CONFIDENCE_MINIMUM = 0.5
WORKING_CATEGORY_EPHEMERAL_TRACE = "ephemeral_trace"
WORKING_CATEGORY_SHORT_LIVED_ASSUMPTION = "short_lived_assumption"
WORKING_CATEGORY_CANDIDATE_RULE = "candidate_rule"
WORKING_CATEGORY_CANDIDATE_PREFERENCE = "candidate_preference"
WORKING_CATEGORY_CANDIDATE_CONSTRAINT = "candidate_constraint"
WORKING_CATEGORY_CONFLICT = "conflict"
WORKING_STATE_CATEGORIES = {
    WORKING_CATEGORY_EPHEMERAL_TRACE,
    WORKING_CATEGORY_SHORT_LIVED_ASSUMPTION,
    WORKING_CATEGORY_CANDIDATE_RULE,
    WORKING_CATEGORY_CANDIDATE_PREFERENCE,
    WORKING_CATEGORY_CANDIDATE_CONSTRAINT,
    WORKING_CATEGORY_CONFLICT,
}
WORKING_CATEGORY_DEFAULT_EXPIRY = {
    WORKING_CATEGORY_EPHEMERAL_TRACE: "2h",
    WORKING_CATEGORY_SHORT_LIVED_ASSUMPTION: "8h",
    WORKING_CATEGORY_CANDIDATE_RULE: "24h",
    WORKING_CATEGORY_CANDIDATE_PREFERENCE: "24h",
    WORKING_CATEGORY_CANDIDATE_CONSTRAINT: "24h",
    WORKING_CATEGORY_CONFLICT: "24h",
}
WORKING_REVIEW_CARD_CATEGORIES = {
    WORKING_CATEGORY_CANDIDATE_CONSTRAINT,
    WORKING_CATEGORY_CONFLICT,
}

WorkingReviewBucket = Literal["none", "ready_to_confirm", "needs_review"]


@dataclass(frozen=True, slots=True)
class WorkingStateLifecycle:
    status: str
    active_for_context: bool
    expired: bool
    review_candidate: bool
    review_due: bool
    near_expiry: bool
    review_card: bool
    review_bucket: WorkingReviewBucket
    recommended_action: str
    reason: str
    score: int


def working_events_path(project_dir: Path | None = None) -> Path:
    return brain_dir(project_dir) / DEFAULT_WORKING_EVENTS_NAME


def working_state_path(project_dir: Path | None = None) -> Path:
    return brain_dir(project_dir) / DEFAULT_WORKING_STATE_NAME


def initial_working_state(project_name: str = "Soul Project") -> WorkingStateDoc:
    return {
        "schema_version": 1,
        "project": project_name,
        "updated_at": utc_now(),
        "items": [],
    }


def load_working_state(project_dir: Path | None = None, project_name: str = "Soul Project") -> WorkingStateDoc:
    path = working_state_path(project_dir)
    if not path.exists():
        return initial_working_state(project_name)
    return cast(WorkingStateDoc, json.loads(path.read_text(encoding="utf-8")))


def save_working_state(doc: WorkingStateDoc, project_dir: Path | None = None) -> None:
    path = working_state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_working_event(event: dict[str, Any], project_dir: Path | None = None) -> None:
    path = working_events_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def route_working_state_from_evidence(
    evidence: dict[str, Any],
    *,
    project_dir: Path | None = None,
    state: StateDoc | None = None,
) -> dict[str, Any]:
    explicit = evidence.get("working_state")
    if isinstance(explicit, dict):
        route = str(explicit.get("route") or "working_state")
        if route == "no_state":
            return {"route": "no_state", "reason": str(explicit.get("reason") or "Explicitly routed away from Working State.")}
        routed = {
            "route": route if route in {"no_state", "working_state"} else "no_state",
            "kind": str(explicit.get("kind") or ""),
            "category": str(explicit.get("category") or ""),
            "review_candidate": explicit.get("review_candidate") if "review_candidate" in explicit else None,
            "review_card": bool(explicit.get("review_card", False)),
            "statement": compact_text(str(explicit.get("statement") or ""), WORKING_STATEMENT_MAX_LENGTH),
            "reason": compact_text(str(explicit.get("reason") or ""), WORKING_REASON_MAX_LENGTH),
            "scope": compact_text(str(explicit.get("scope") or evidence.get("task") or ""), WORKING_SCOPE_MAX_LENGTH),
            "confidence": float_value(explicit.get("confidence"), default=-1.0),
            "expires": explicit.get("expires"),
            "review_after": explicit.get("review_after"),
        }
    else:
        routed = infer_working_route(evidence)

    if routed["route"] == "no_state":
        return {"route": "no_state", "reason": routed.get("reason", "No working-state change detected.")}

    evidence_refs = evidence.get("evidence_refs")
    refs = [ref for ref in evidence_refs if isinstance(ref, dict)] if isinstance(evidence_refs, list) else []
    if not refs:
        return {"route": "no_state", "reason": "Working State requires evidence refs."}

    statement = compact_text(str(routed.get("statement") or ""), WORKING_STATEMENT_MAX_LENGTH)
    scope = compact_text(str(routed.get("scope") or ""), WORKING_SCOPE_MAX_LENGTH)
    reason = compact_text(str(routed.get("reason") or ""), WORKING_REASON_MAX_LENGTH)
    if not statement or not scope or not reason:
        return {"route": "no_state", "reason": "Working State requires statement, reason, and scope."}
    kind = str(routed.get("kind") or "")
    if kind not in WORKING_STATE_KINDS:
        return {"route": "no_state", "reason": "Working State requires a valid kind."}
    confidence = float_value(routed.get("confidence"), default=-1.0)
    if confidence < WORKING_CONFIDENCE_MINIMUM or confidence > 1.0:
        return {"route": "no_state", "reason": "Working State requires confidence between 0.5 and 1.0."}

    current_state = state or load_state(project_dir)
    duplicate_id = duplicate_state_item_id(current_state, statement)
    if duplicate_id:
        return {"route": "no_state", "reason": f"Already represented in accepted state: {duplicate_id}."}

    conflicts = accepted_state_conflicts(current_state, statement)
    status = WORKING_STATUS_CONFLICT_NEEDS_REVIEW if conflicts else WORKING_STATUS_WORKING
    review_card = bool(routed.get("review_card", False))
    category = working_category(
        kind=kind,
        raw_category=str(routed.get("category") or ""),
        status=status,
        review_card=review_card,
    )
    review_candidate = working_review_candidate(
        category=category,
        review_card=review_card,
        explicit_value=routed.get("review_candidate"),
    )
    expires = str(routed.get("expires") or default_expiry_for_working_category(category))
    review_after = str(routed.get("review_after") or default_review_after_for_working_category(category, review_card=review_card))
    return {
        "route": "working_state",
        "kind": kind,
        "category": category,
        "review_candidate": review_candidate,
        "review_card": review_card,
        "statement": statement,
        "reason": reason,
        "scope": scope,
        "confidence": confidence,
        "expires_at": resolve_expiry(expires),
        "review_after": resolve_review_after(review_after),
        "evidence_refs": refs,
        "status": status,
        "conflicts_with": conflicts,
    }


def infer_working_route(evidence: dict[str, Any]) -> dict[str, Any]:
    return {"route": "no_state", "reason": "Working State requires an explicit structured candidate."}


def working_category(*, kind: str, raw_category: str, status: str, review_card: bool) -> str:
    if status == WORKING_STATUS_CONFLICT_NEEDS_REVIEW:
        return WORKING_CATEGORY_CONFLICT
    if raw_category in WORKING_STATE_CATEGORIES:
        return raw_category
    if kind == WORKING_KIND_CONSTRAINT:
        return WORKING_CATEGORY_CANDIDATE_CONSTRAINT
    if kind == WORKING_KIND_PREFERENCE:
        return WORKING_CATEGORY_CANDIDATE_PREFERENCE
    if review_card:
        return WORKING_CATEGORY_CANDIDATE_RULE
    return WORKING_CATEGORY_SHORT_LIVED_ASSUMPTION


def working_review_candidate(*, category: str, review_card: bool, explicit_value: Any) -> bool:
    if category == WORKING_CATEGORY_CONFLICT:
        return True
    if explicit_value is not None:
        return bool(explicit_value)
    return review_card or category in WORKING_REVIEW_CARD_CATEGORIES


def default_expiry_for_working_category(category: str) -> str:
    return WORKING_CATEGORY_DEFAULT_EXPIRY.get(category, DEFAULT_WORKING_EXPIRY)


def default_review_after_for_working_category(category: str, *, review_card: bool) -> str:
    if review_card or category in WORKING_REVIEW_CARD_CATEGORIES:
        return "now"
    return DEFAULT_WORKING_REVIEW_AFTER


def resolve_expiry(raw: str) -> str:
    now = datetime.now(UTC).replace(microsecond=0)
    if raw in {"", "none", "never"}:
        return ""
    duration = parse_duration(raw)
    if duration is not None:
        return (now + duration).isoformat().replace("+00:00", "Z")
    if raw == "end_of_day":
        local_now = datetime.now().astimezone().replace(microsecond=0)
        end = local_now.replace(hour=22, minute=0, second=0)
        if local_now >= end:
            end = local_now + timedelta(hours=4)
        return end.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return raw
    except ValueError:
        return (now + timedelta(hours=8)).isoformat().replace("+00:00", "Z")


def resolve_review_after(raw: str) -> str:
    now = datetime.now(UTC).replace(microsecond=0)
    if raw in {"", "none", "never"}:
        return ""
    if raw == "now":
        return now.isoformat().replace("+00:00", "Z")
    duration = parse_duration(raw)
    if duration is not None:
        return (now + duration).isoformat().replace("+00:00", "Z")
    if raw == "end_of_day":
        local_now = datetime.now().astimezone().replace(microsecond=0)
        review_at = local_now.replace(hour=22, minute=0, second=0)
        if local_now >= review_at:
            return now.isoformat().replace("+00:00", "Z")
        return review_at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return raw
    except ValueError:
        return now.isoformat().replace("+00:00", "Z")


def parse_duration(raw: str) -> timedelta | None:
    match = re.fullmatch(r"\s*(\d+)\s*([mhd])\s*", raw.lower())
    if match is None:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def float_value(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def duplicate_state_item_id(state: StateDoc, statement: str) -> str:
    new_key = normalize_for_compare(statement)
    for item in state.get("current_state", {}).get("state_items", []):
        existing = normalize_for_compare(str(item.get("statement") or ""))
        if existing and (existing == new_key or existing in new_key or new_key in existing):
            return str(item.get("id") or "")
    return ""


def accepted_state_conflicts(state: StateDoc, statement: str) -> list[str]:
    lowered = statement.lower()
    conflicts: list[str] = []
    for item in state.get("current_state", {}).get("state_items", []):
        existing = str(item.get("statement") or "").lower()
        if not existing:
            continue
        if (any(signal in lowered for signal in CONFLICT_NEGATION_SIGNALS)) and any(
            token and token in existing for token in meaningful_tokens(statement)
        ):
            conflicts.append(str(item.get("id") or "accepted-state-item"))
    return conflicts


def meaningful_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^A-Za-z0-9_\u4e00-\u9fff]+", text.lower())
        if len(token) >= WORKING_TOKEN_MIN_LENGTH
    ]


def normalize_for_compare(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff]+", "", text.lower())


def upsert_working_state_from_evidence(
    project_dir: Path,
    evidence: dict[str, Any],
    *,
    state: StateDoc | None = None,
) -> dict[str, Any]:
    routed = route_working_state_from_evidence(evidence, project_dir=project_dir, state=state)
    if routed.get("route") != "working_state":
        append_working_event({"event": "no_state", "created_at": utc_now(), "route": routed, "evidence": evidence}, project_dir)
        return {"route": "no_state", "reason": routed.get("reason", "")}

    doc = load_working_state(project_dir, project_name=project_dir.name)
    item = working_item_from_route(routed, evidence)
    superseded = merge_or_append_working_item(doc, item)
    doc["updated_at"] = utc_now()
    save_working_state(doc, project_dir)
    append_working_event(
        {
            "event": "upsert",
            "created_at": utc_now(),
            "item": item,
            "superseded": superseded,
        },
        project_dir,
    )
    return {"route": "working_state", "item": item, "superseded": superseded}


def working_item_from_route(route: dict[str, Any], evidence: dict[str, Any]) -> WorkingStateItem:
    now = utc_now()
    statement = str(route["statement"])
    item_id = "working-" + stable_state_item_id(statement).removeprefix("knowledge-")
    return {
        "id": item_id,
        "status": str(route.get("status") or WORKING_STATUS_WORKING),
        "kind": str(route.get("kind") or WORKING_KIND_PROJECT_FACT),
        "category": str(route.get("category") or WORKING_CATEGORY_SHORT_LIVED_ASSUMPTION),
        "statement": statement,
        "reason": str(route.get("reason") or ""),
        "scope": str(route.get("scope") or ""),
        "confidence": float_value(route.get("confidence"), default=0.75),
        "review_candidate": bool(route.get("review_candidate", True)),
        "review_card": bool(route.get("review_card", False)),
        "evidence_refs": cast(list[dict[str, Any]], route.get("evidence_refs") or []),
        "source": str(evidence.get("source") or ""),
        "task": str(evidence.get("task") or ""),
        "created_at": now,
        "updated_at": now,
        "expires_at": str(route.get("expires_at") or resolve_expiry(DEFAULT_WORKING_EXPIRY)),
        "review_after": str(route.get("review_after") or resolve_review_after(DEFAULT_WORKING_REVIEW_AFTER)),
        "supersedes": [],
        "conflicts_with": cast(list[str], route.get("conflicts_with") or []),
    }


def merge_or_append_working_item(doc: WorkingStateDoc, item: WorkingStateItem) -> list[str]:
    superseded: list[str] = []
    new_key = normalize_for_compare(str(item.get("statement") or ""))
    for existing in doc.get("items", []):
        existing_key = normalize_for_compare(str(existing.get("statement") or ""))
        same_scope = str(existing.get("scope") or "") == item.get("scope")
        item_id = str(item.get("id") or "")
        if existing.get("id") == item_id or (same_scope and existing_key and (existing_key in new_key or new_key in existing_key)):
            superseded.append(str(existing.get("id") or ""))
            merged_refs = merge_refs(
                cast(list[dict[str, Any]], existing.get("evidence_refs") or []),
                cast(list[dict[str, Any]], item.get("evidence_refs") or []),
            )
            created_at = str(existing.get("created_at") or item.get("created_at") or utc_now())
            existing.update(item)
            existing["created_at"] = created_at
            existing["updated_at"] = utc_now()
            existing["evidence_refs"] = merged_refs
            existing["supersedes"] = sorted(set(cast(list[str], existing.get("supersedes") or []) + superseded))
            return superseded
    doc.setdefault("items", []).append(item)
    return superseded


def merge_refs(*ref_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for refs in ref_lists:
        for ref in refs:
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
    return merged


def classify_working_item_lifecycle(
    item: WorkingStateItem,
    *,
    now: datetime | None = None,
    near_expiry_hours: int = 4,
) -> WorkingStateLifecycle:
    current_time = now or datetime.now(UTC)
    status = str(item.get("status") or "")
    expired = is_working_item_expired(item, now=current_time)
    review_candidate = bool(item.get("review_candidate", False))
    review_due = is_working_item_review_due(item, now=current_time)
    near_expiry = is_working_item_near_expiry(item, hours=near_expiry_hours, now=current_time)
    review_card = bool(item.get("review_card", False))
    category = str(item.get("category") or "")
    active_for_context = status == WORKING_STATUS_WORKING and not expired

    review_bucket: WorkingReviewBucket = "none"
    recommended_action = ""
    reason = ""
    score = 0
    if expired and status in {WORKING_STATUS_WORKING, WORKING_STATUS_CONFLICT_NEEDS_REVIEW}:
        recommended_action = "expire"
        reason = "Working State expired before it needed a user decision."
    elif review_candidate and status == WORKING_STATUS_CONFLICT_NEEDS_REVIEW:
        review_bucket = "needs_review"
        recommended_action = "review"
        reason = "Working State conflicts with accepted state and should not silently affect future turns."
        score = 100
    elif review_candidate and review_card and review_due:
        review_bucket = "ready_to_confirm"
        recommended_action = "accept"
        reason = "Working State was explicitly marked as worth confirming in the low-noise review card."
        score = 75
    elif review_candidate and category == WORKING_CATEGORY_CANDIDATE_CONSTRAINT and review_due:
        review_bucket = "needs_review"
        recommended_action = "accept"
        reason = "Candidate constraint may affect future agent behavior and needs an explicit keep or dismiss decision."
        score = 65

    return WorkingStateLifecycle(
        status=status,
        active_for_context=active_for_context,
        expired=expired,
        review_candidate=review_candidate,
        review_due=review_due,
        near_expiry=near_expiry,
        review_card=review_card,
        review_bucket=review_bucket,
        recommended_action=recommended_action,
        reason=reason,
        score=score,
    )


def is_working_item_active(item: WorkingStateItem, *, now: datetime | None = None) -> bool:
    return classify_working_item_lifecycle(item, now=now).active_for_context


def expire_due_working_items(
    project_dir: Path | None = None,
    *,
    now: datetime | None = None,
    reason: str = "Working State TTL elapsed.",
) -> list[str]:
    project = project_dir or Path.cwd()
    doc = load_working_state(project)
    current_time = now or datetime.now(UTC)
    expired_ids: list[str] = []
    for item in doc.get("items", []):
        if item.get("status") not in {WORKING_STATUS_WORKING, WORKING_STATUS_CONFLICT_NEEDS_REVIEW}:
            continue
        if not is_working_item_expired(item, now=current_time):
            continue
        item["status"] = WORKING_STATUS_EXPIRED
        item["updated_at"] = utc_now()
        expired_ids.append(str(item.get("id") or ""))
    if not expired_ids:
        return []
    doc["updated_at"] = utc_now()
    save_working_state(doc, project)
    append_working_event(
        {
            "event": WORKING_STATUS_EXPIRED,
            "created_at": utc_now(),
            "reason": reason,
            "working_ids": expired_ids,
        },
        project,
    )
    return expired_ids


def is_working_item_expired(item: WorkingStateItem, *, now: datetime | None = None) -> bool:
    raw_expiry = item.get("expires_at")
    if not raw_expiry:
        return False
    try:
        expiry = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
    except ValueError:
        return False
    return expiry <= (now or datetime.now(UTC))


def is_working_item_review_due(item: WorkingStateItem, *, now: datetime | None = None) -> bool:
    raw_review_after = item.get("review_after")
    if not raw_review_after:
        return False
    try:
        review_after = datetime.fromisoformat(str(raw_review_after).replace("Z", "+00:00"))
    except ValueError:
        return True
    return review_after <= (now or datetime.now(UTC))


def is_working_item_near_expiry(item: WorkingStateItem, *, hours: int, now: datetime | None = None) -> bool:
    raw_expiry = item.get("expires_at")
    if not raw_expiry:
        return False
    try:
        expiry = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
    except ValueError:
        return False
    current_time = now or datetime.now(UTC)
    return current_time < expiry <= current_time + timedelta(hours=hours)


def project_working_state_items(
    project_dir: Path | None = None,
    *,
    task: str = "",
    limit: int = 5,
    now: datetime | None = None,
) -> list[WorkingStateItem]:
    doc = load_working_state(project_dir)
    active = [
        item
        for item in doc.get("items", [])
        if classify_working_item_lifecycle(item, now=now).active_for_context
    ]
    if task:
        relevant = [
            item for item in active
            if state_item_matches_task({"id": str(item.get("id") or ""), "statement": item.get("statement", ""), "kind": "working_state"}, task)
            or state_item_matches_task({"id": str(item.get("id") or ""), "statement": item.get("scope", ""), "kind": "working_state"}, task)
        ]
        if relevant:
            active = relevant
    active.sort(key=lambda item: (str(item.get("updated_at", "")), str(item.get("id", ""))), reverse=True)
    return active[:limit]


def format_working_state_context(project_dir: Path | None = None, *, task: str = "", limit: int = 5) -> str:
    items = project_working_state_items(project_dir, task=task, limit=limit)
    if not items:
        return ""
    lines = ["", "Working State, unconfirmed:"]
    due_items: list[WorkingStateItem] = []
    for item in items:
        expiry = f", expires={item.get('expires_at')}" if item.get("expires_at") else ""
        lifecycle = classify_working_item_lifecycle(item)
        review = ", review-due" if lifecycle.review_due else ""
        if review:
            due_items.append(item)
        lines.append(f"- [working{review}{expiry}] {item.get('statement', '')}")
    if due_items:
        lines.extend(["", "Working State Review Due:"])
        for item in due_items:
            lines.append(f"- Review `{item.get('id', '')}`: {item.get('scope', '')}")
    return "\n".join(lines)


def review_working_items(project_dir: Path | None = None, *, limit: int = 10) -> list[WorkingStateItem]:
    doc = load_working_state(project_dir)
    items = [
        item
        for item in doc.get("items", [])
        if item.get("status") in {WORKING_STATUS_WORKING, WORKING_STATUS_CONFLICT_NEEDS_REVIEW}
        and bool(item.get("review_candidate", False))
    ]
    items.sort(key=lambda item: (str(item.get("updated_at", "")), str(item.get("id", ""))), reverse=True)
    return items[:limit]


def promote_working_item(project_dir: Path | None, working_id: str, *, confirmed_by: str = "user") -> dict[str, Any]:
    project = project_dir or Path.cwd()
    doc = load_working_state(project)
    item = next((candidate for candidate in doc.get("items", []) if candidate.get("id") == working_id), None)
    if item is None:
        raise ValueError(f"Working State item not found: {working_id}")
    evidence = {
        "source": item.get("source") or "working_state",
        "task": item.get("task") or item.get("scope") or "Working State promotion",
        "summary": item.get("statement") or "",
        "content": item.get("reason") or item.get("statement") or "",
        "why_remember": item.get("reason") or "Promoted from Working State after review.",
        "evidence_refs": item.get("evidence_refs") or [],
        "state_item": {
            "id": stable_state_item_id(str(item.get("statement") or "")),
            "kind": accepted_state_kind_for_working_item(item),
            "statement": item.get("statement") or "",
            "status": PATCH_STATUS_NEEDS_REVIEW,
            "priority": infer_priority(str(item.get("statement") or "")),
            "confidence": float_value(item.get("confidence"), default=0.65),
            "why_remember": item.get("reason") or why_remember_for_statement(str(item.get("statement") or "")),
        },
    }
    proposal = propose_patch(load_state(project), evidence, source="working_state")
    append_patch_proposal(proposal, project)
    item["status"] = WORKING_STATUS_PROMOTED
    item["updated_at"] = utc_now()
    doc["updated_at"] = utc_now()
    save_working_state(doc, project)
    append_working_event(
        {
            "event": WORKING_STATUS_PROMOTED,
            "created_at": utc_now(),
            "working_id": working_id,
            "patch_id": proposal["id"],
            "confirmed_by": confirmed_by,
        },
        project,
    )
    return {"working_item": item, "patch_proposal": proposal}


def accepted_state_kind_for_working_item(item: WorkingStateItem) -> str:
    kind = str(item.get("kind") or "")
    return WORKING_KIND_TO_STATE_KIND.get(kind) or infer_state_kind(str(item.get("statement") or "")) or STATE_KIND_ACCEPTED_BELIEF


def expire_working_item(project_dir: Path | None, working_id: str, *, reason: str = "") -> WorkingStateItem:
    return set_working_item_status(project_dir, working_id, status=WORKING_STATUS_EXPIRED, reason=reason)


def reject_working_item(project_dir: Path | None, working_id: str, *, reason: str = "") -> WorkingStateItem:
    return set_working_item_status(project_dir, working_id, status=WORKING_STATUS_REJECTED, reason=reason)


def edit_working_item(
    project_dir: Path | None,
    working_id: str,
    *,
    statement: str | None = None,
    reason: str | None = None,
    scope: str | None = None,
    review_after: str | None = None,
    expires_at: str | None = None,
    updated_by: str = "user",
) -> WorkingStateItem:
    project = project_dir or Path.cwd()
    doc = load_working_state(project)
    item = next((candidate for candidate in doc.get("items", []) if candidate.get("id") == working_id), None)
    if item is None:
        raise ValueError(f"Working State item not found: {working_id}")
    before = dict(item)
    if statement is not None:
        item["statement"] = compact_text(statement, WORKING_STATEMENT_MAX_LENGTH)
    if reason is not None:
        item["reason"] = compact_text(reason, WORKING_REASON_MAX_LENGTH)
    if scope is not None:
        item["scope"] = compact_text(scope, WORKING_SCOPE_MAX_LENGTH)
    if review_after is not None:
        item["review_after"] = resolve_review_after(review_after)
    if expires_at is not None:
        item["expires_at"] = resolve_expiry(expires_at)
    item["updated_at"] = utc_now()
    doc["updated_at"] = utc_now()
    save_working_state(doc, project)
    append_working_event(
        {
            "event": "edited",
            "created_at": utc_now(),
            "working_id": working_id,
            "updated_by": updated_by,
            "before": before,
            "after": item,
        },
        project,
    )
    return item


def set_working_item_status(
    project_dir: Path | None,
    working_id: str,
    *,
    status: str,
    reason: str = "",
) -> WorkingStateItem:
    project = project_dir or Path.cwd()
    doc = load_working_state(project)
    item = next((candidate for candidate in doc.get("items", []) if candidate.get("id") == working_id), None)
    if item is None:
        raise ValueError(f"Working State item not found: {working_id}")
    item["status"] = status
    item["updated_at"] = utc_now()
    doc["updated_at"] = utc_now()
    save_working_state(doc, project)
    append_working_event({"event": status, "created_at": utc_now(), "working_id": working_id, "reason": reason}, project)
    return item
