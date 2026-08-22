from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from soul.services.shared.constants import (
    PATCH_REVIEW_AUTO_ACCEPT,
    PATCH_REVIEW_NEEDS_REVIEW,
    PATCH_REVIEW_REJECT,
    PATCH_STATUS_APPLIED,
    PATCH_STATUS_PROPOSED,
    PATCH_STATUS_REJECTED,
    WORKING_STATUS_CONFLICT_NEEDS_REVIEW,
    WORKING_STATUS_WORKING,
)
from soul.services.shared.state_types import PatchProposal, WorkingStateItem
from soul.services.shared.text import compact_text
from soul.services.state_core.state_store import load_patch_proposals, load_state, utc_now
from soul.services.state_core.working_state import load_working_state


CandidateBucket = Literal["ready_to_confirm", "needs_review"]


def build_review_card(
    project_dir: Path | None = None,
    *,
    limit: int = 5,
    near_expiry_hours: int = 4,
    now: datetime | None = None,
) -> dict[str, Any]:
    project = project_dir or Path.cwd()
    state = load_state(project, project_name=project.name)
    current_time = now or datetime.now(UTC)
    candidates = review_candidates(project, near_expiry_hours=near_expiry_hours, now=current_time)
    candidates.sort(key=candidate_sort_key, reverse=True)
    selected = candidates[:limit]
    ready = [candidate for candidate in selected if candidate["bucket"] == "ready_to_confirm"]
    needs_review = [candidate for candidate in selected if candidate["bucket"] == "needs_review"]
    return {
        "schema_version": 1,
        "project": state.get("project", project.name),
        "project_dir": str(project),
        "generated_at": utc_now(),
        "limit": limit,
        "has_reviewable_content": bool(selected),
        "counts": {
            "ready_to_confirm": len(ready),
            "needs_review": len(needs_review),
            "total": len(selected),
            "available": len(candidates),
        },
        "ready_to_confirm": ready,
        "needs_review": needs_review,
    }


def review_candidates(
    project_dir: Path,
    *,
    near_expiry_hours: int,
    now: datetime,
) -> list[dict[str, Any]]:
    return patch_candidates(project_dir) + working_state_candidates(
        project_dir,
        near_expiry_hours=near_expiry_hours,
        now=now,
    )


def patch_candidates(project_dir: Path) -> list[dict[str, Any]]:
    latest = latest_patch_records(load_patch_proposals(project_dir))
    candidates: list[dict[str, Any]] = []
    for proposal in latest:
        if proposal.get("status", PATCH_STATUS_PROPOSED) != PATCH_STATUS_PROPOSED:
            continue
        recommendation = str(proposal.get("review_recommendation") or PATCH_REVIEW_NEEDS_REVIEW)
        if recommendation == PATCH_REVIEW_AUTO_ACCEPT:
            bucket: CandidateBucket = "ready_to_confirm"
            recommended_action = "accept"
            review_reason = "State Patch is complete enough to confirm with low decision cost."
            score = 80
        elif recommendation == PATCH_REVIEW_REJECT:
            bucket = "needs_review"
            recommended_action = "reject"
            review_reason = "State Patch has weak or missing durable state value."
            score = 30
        else:
            bucket = "needs_review"
            recommended_action = "review"
            review_reason = "State Patch affects future agent behavior and needs explicit judgment."
            score = 60
        candidates.append(
            {
                "id": f"patch:{proposal.get('id', '')}",
                "bucket": bucket,
                "source_type": "patch_proposal",
                "source_id": proposal.get("id", ""),
                "title": compact_text(str(proposal.get("title") or "State Patch"), 80),
                "statement": patch_statement(proposal),
                "review_reason": review_reason,
                "recommended_action": recommended_action,
                "recommendation": recommendation,
                "decision_cost": "low" if bucket == "ready_to_confirm" else "medium",
                "score": score,
                "created_at": proposal.get("created_at", ""),
                "updated_at": proposal.get("updated_at") or proposal.get("created_at", ""),
                "evidence": proposal_evidence(proposal),
                "actions": patch_actions(recommended_action),
            }
        )
    return candidates


def latest_patch_records(proposals: list[PatchProposal]) -> list[PatchProposal]:
    latest: dict[str, PatchProposal] = {}
    order: list[str] = []
    for proposal in proposals:
        proposal_id = str(proposal.get("id") or "")
        if not proposal_id:
            continue
        if proposal_id not in latest:
            order.append(proposal_id)
        latest[proposal_id] = proposal
    terminal = {PATCH_STATUS_APPLIED, PATCH_STATUS_REJECTED}
    return [latest[proposal_id] for proposal_id in order if latest[proposal_id].get("status") not in terminal]


def patch_statement(proposal: PatchProposal) -> str:
    points = proposal.get("knowledge_points") or []
    statements = [
        str(point.get("statement") or "")
        for point in points
        if isinstance(point, dict) and str(point.get("statement") or "").strip()
    ]
    if statements:
        return compact_text(" ".join(statements[:2]), 240)
    evidence = proposal.get("evidence") if isinstance(proposal.get("evidence"), dict) else {}
    return compact_text(str(evidence.get("summary") or proposal.get("why_remember") or proposal.get("title") or ""), 240)


def proposal_evidence(proposal: PatchProposal) -> dict[str, Any]:
    raw_refs = proposal.get("refs")
    refs = raw_refs if isinstance(raw_refs, dict) else {}
    raw_evidence_refs = refs.get("evidence_refs")
    evidence_refs = raw_evidence_refs if isinstance(raw_evidence_refs, list) else []
    raw_raw_evidence = refs.get("raw_evidence")
    raw = raw_raw_evidence if isinstance(raw_raw_evidence, dict) else {}
    return {
        "summary": compact_text(str(raw.get("summary") or proposal.get("why_remember") or ""), 200),
        "refs": evidence_refs,
        "source": proposal.get("source", ""),
    }


def patch_actions(recommended_action: str) -> list[str]:
    actions = ["accept", "edit", "reject", "evidence"]
    if recommended_action == "reject":
        return ["reject", "edit", "accept", "evidence"]
    return actions


def working_state_candidates(
    project_dir: Path,
    *,
    near_expiry_hours: int,
    now: datetime,
) -> list[dict[str, Any]]:
    doc = load_working_state(project_dir, project_name=project_dir.name)
    candidates: list[dict[str, Any]] = []
    for item in doc.get("items", []):
        status = str(item.get("status") or "")
        if status not in {WORKING_STATUS_WORKING, WORKING_STATUS_CONFLICT_NEEDS_REVIEW}:
            continue
        due = is_due(str(item.get("review_after") or ""), now=now)
        near_expiry = is_near_expiry(str(item.get("expires_at") or ""), hours=near_expiry_hours, now=now)
        review_card = bool(item.get("review_card", False))
        if status != WORKING_STATUS_CONFLICT_NEEDS_REVIEW and not due and not near_expiry:
            continue
        if status == WORKING_STATUS_CONFLICT_NEEDS_REVIEW:
            bucket: CandidateBucket = "needs_review"
            recommended_action = "review"
            reason = "Working State conflicts with accepted state and should not silently affect future turns."
            score = 100
        elif near_expiry and not due:
            bucket = "needs_review"
            recommended_action = "review"
            reason = "Working State is close to expiry; decide whether to keep, accept, or reject it."
            score = 70
        elif review_card:
            bucket = "ready_to_confirm"
            recommended_action = "accept"
            reason = "Working State was explicitly marked as worth confirming in the low-noise review card."
            score = 75
        else:
            continue
        candidates.append(working_candidate(item, bucket, recommended_action, reason, score))
    return candidates


def working_candidate(
    item: WorkingStateItem,
    bucket: CandidateBucket,
    recommended_action: str,
    reason: str,
    score: int,
) -> dict[str, Any]:
    return {
        "id": f"working:{item.get('id', '')}",
        "bucket": bucket,
        "source_type": "working_state",
        "source_id": item.get("id", ""),
        "title": compact_text(str(item.get("scope") or "Working State"), 80),
        "statement": compact_text(str(item.get("statement") or ""), 240),
        "review_reason": reason,
        "recommended_action": recommended_action,
        "recommendation": "confirm" if bucket == "ready_to_confirm" else "needs_review",
        "decision_cost": "low" if bucket == "ready_to_confirm" else "medium",
        "score": score,
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
        "expires_at": item.get("expires_at", ""),
        "review_after": item.get("review_after", ""),
        "conflicts_with": item.get("conflicts_with", []),
        "evidence": {
            "summary": compact_text(str(item.get("reason") or ""), 200),
            "refs": item.get("evidence_refs", []),
            "source": item.get("source", ""),
        },
        "actions": ["accept", "edit", "reject", "snooze", "evidence"],
    }


def is_due(raw: str, *, now: datetime) -> bool:
    value = parse_datetime(raw)
    return value is not None and value <= now


def is_near_expiry(raw: str, *, hours: int, now: datetime) -> bool:
    value = parse_datetime(raw)
    if value is None:
        return False
    return now < value <= now + timedelta(hours=hours)


def parse_datetime(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, str]:
    return (int(candidate.get("score") or 0), str(candidate.get("updated_at") or candidate.get("created_at") or ""))
