from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from soul.services.shared.constants import (
    PATCH_STATUS_NEEDS_REVIEW,
    STATE_KIND_ACCEPTED_BELIEF,
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


DEFAULT_WORKING_EVENTS_NAME = "working_events.jsonl"
DEFAULT_WORKING_STATE_NAME = "working_state.json"


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
        routed = {
            "route": route if route in {"no_state", "working_state"} else "no_state",
            "review_candidate": bool(explicit.get("review_candidate", True)),
            "review_card": bool(explicit.get("review_card", False)),
            "statement": compact_text(str(explicit.get("statement") or ""), 320),
            "reason": compact_text(str(explicit.get("reason") or ""), 240),
            "scope": compact_text(str(explicit.get("scope") or evidence.get("task") or ""), 120),
            "expires": str(explicit.get("expires") or "end_of_day"),
            "review_after": str(explicit.get("review_after") or "end_of_day"),
        }
    else:
        routed = infer_working_route(evidence)

    if routed["route"] == "no_state":
        return {"route": "no_state", "reason": routed.get("reason", "No working-state change detected.")}

    evidence_refs = evidence.get("evidence_refs")
    refs = [ref for ref in evidence_refs if isinstance(ref, dict)] if isinstance(evidence_refs, list) else []
    if not refs:
        return {"route": "no_state", "reason": "Working State requires evidence refs."}

    statement = compact_text(str(routed.get("statement") or ""), 320)
    scope = compact_text(str(routed.get("scope") or ""), 120)
    reason = compact_text(str(routed.get("reason") or ""), 240)
    if not statement or not scope or not reason:
        return {"route": "no_state", "reason": "Working State requires statement, reason, and scope."}

    current_state = state or load_state(project_dir)
    duplicate_id = duplicate_state_item_id(current_state, statement)
    if duplicate_id:
        return {"route": "no_state", "reason": f"Already represented in accepted state: {duplicate_id}."}

    conflicts = accepted_state_conflicts(current_state, statement)
    return {
        "route": "working_state",
        "review_candidate": bool(routed.get("review_candidate", True)),
        "review_card": bool(routed.get("review_card", False)),
        "statement": statement,
        "reason": reason,
        "scope": scope,
        "expires_at": resolve_expiry(str(routed.get("expires") or "end_of_day")),
        "review_after": resolve_review_after(str(routed.get("review_after") or "end_of_day")),
        "evidence_refs": refs,
        "status": WORKING_STATUS_CONFLICT_NEEDS_REVIEW if conflicts else WORKING_STATUS_WORKING,
        "conflicts_with": conflicts,
    }


def infer_working_route(evidence: dict[str, Any]) -> dict[str, Any]:
    text = evidence_text(evidence)
    if not text:
        return {"route": "no_state", "reason": "No evidence text to route."}
    if looks_episodic(text):
        return {"route": "no_state", "reason": "Evidence describes an episode without a working assumption."}

    statement = extract_working_statement(text)
    if not statement:
        return {"route": "no_state", "reason": "No working-state change detected."}

    return {
        "route": "working_state",
        "review_candidate": True,
        "statement": statement,
        "reason": "Not retaining this working assumption may cause the next turn to repeat or follow an outdated direction.",
        "scope": compact_text(str(evidence.get("task") or evidence.get("source") or "current task"), 120),
        "expires": "end_of_day",
        "review_after": "end_of_day",
    }


def evidence_text(evidence: dict[str, Any]) -> str:
    parts = [
        str(evidence.get("summary") or ""),
        str(evidence.get("content") or ""),
        str(evidence.get("outcome") or ""),
    ]
    seen: set[str] = set()
    unique_parts: list[str] = []
    for part in (part.strip() for part in parts if part and part.strip()):
        if part in seen:
            continue
        seen.add(part)
        unique_parts.append(part)
    return " ".join(unique_parts)


def looks_episodic(text: str) -> bool:
    lowered = text.lower().strip()
    episodic_prefixes = (
        "created ",
        "configured ",
        "updated ",
        "added ",
        "ran ",
        "tested ",
        "verified ",
        "fixed ",
        "read ",
        "reviewed ",
        "今天阅读",
        "阅读了",
        "查看了",
        "运行了",
    )
    return lowered.startswith(episodic_prefixes) and not contains_working_change_signal(text)


def contains_working_change_signal(text: str) -> bool:
    lowered = text.lower()
    signals = (
        "当前",
        "主线",
        "转向",
        "不再",
        "不能等同",
        "已诊断",
        "诊断出",
        "瓶颈",
        "优先",
        "暂定",
        "先验证",
        "先评估",
        "先考虑",
        "后续",
        "下一步",
        "should",
        "must",
        "default",
        "do not",
        "don't",
        "avoid",
        "qnet",
    )
    return any(signal in lowered for signal in signals)


def extract_working_statement(text: str) -> str:
    normalized = " ".join(text.replace("\n", " ").split())
    sentences = [
        sentence.strip(" -")
        for sentence in re.split(r"(?<=[.!?。！？])\s+|;\s+|；\s+", normalized)
        if sentence.strip(" -")
    ]
    selected = [sentence for sentence in sentences if contains_working_change_signal(sentence)]
    if not selected and contains_working_change_signal(normalized):
        selected = [normalized]
    return compact_text(" ".join(selected[:2]), 320)


def resolve_expiry(raw: str) -> str:
    now = datetime.now(UTC).replace(microsecond=0)
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
        if ("不再" in statement or "do not" in lowered or "don't" in lowered) and any(
            token and token in existing for token in meaningful_tokens(statement)
        ):
            conflicts.append(str(item.get("id") or "accepted-state-item"))
    return conflicts


def meaningful_tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^A-Za-z0-9_\u4e00-\u9fff]+", text.lower()) if len(token) >= 4]


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
        "statement": statement,
        "reason": str(route.get("reason") or ""),
        "scope": str(route.get("scope") or ""),
        "review_candidate": bool(route.get("review_candidate", True)),
        "review_card": bool(route.get("review_card", False)),
        "evidence_refs": cast(list[dict[str, Any]], route.get("evidence_refs") or []),
        "source": str(evidence.get("source") or ""),
        "task": str(evidence.get("task") or ""),
        "created_at": now,
        "updated_at": now,
        "expires_at": str(route.get("expires_at") or resolve_expiry("end_of_day")),
        "review_after": str(route.get("review_after") or resolve_review_after("end_of_day")),
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


def is_working_item_active(item: WorkingStateItem, *, now: datetime | None = None) -> bool:
    if item.get("status") != WORKING_STATUS_WORKING:
        return False
    raw_expiry = item.get("expires_at")
    if not raw_expiry:
        return True
    try:
        expiry = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
    except ValueError:
        return True
    return expiry > (now or datetime.now(UTC))


def is_working_item_review_due(item: WorkingStateItem, *, now: datetime | None = None) -> bool:
    raw_review_after = item.get("review_after")
    if not raw_review_after:
        return False
    try:
        review_after = datetime.fromisoformat(str(raw_review_after).replace("Z", "+00:00"))
    except ValueError:
        return True
    return review_after <= (now or datetime.now(UTC))


def project_working_state_items(
    project_dir: Path | None = None,
    *,
    task: str = "",
    limit: int = 5,
    now: datetime | None = None,
) -> list[WorkingStateItem]:
    doc = load_working_state(project_dir)
    active = [item for item in doc.get("items", []) if is_working_item_active(item, now=now)]
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
        review = ", review-due" if is_working_item_review_due(item) else ""
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
            "kind": infer_state_kind(str(item.get("statement") or "")) or STATE_KIND_ACCEPTED_BELIEF,
            "statement": item.get("statement") or "",
            "status": PATCH_STATUS_NEEDS_REVIEW,
            "priority": infer_priority(str(item.get("statement") or "")),
            "confidence": 0.65,
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
        item["statement"] = compact_text(statement, 320)
    if reason is not None:
        item["reason"] = compact_text(reason, 240)
    if scope is not None:
        item["scope"] = compact_text(scope, 120)
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
