from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from soul.services.shared.constants import (
    HOST_SOUL_HTTP_API,
    PATCH_STATUS_ACCEPTED,
    PATCH_STATUS_APPLIED,
    PATCH_STATUS_REJECTED,
)
from soul.services.shared.state_types import PatchProposal, StateDoc
from soul.services.state_core.proposals import apply_patch_proposal, append_patch_status, edit_patch_proposal
from soul.services.state_core.state_store import find_patch_proposal, load_state, save_state
from soul.services.state_core.working_state import edit_working_item, promote_working_item, reject_working_item


def accept_review_candidate(
    project_dir: Path,
    candidate_id: str,
    *,
    confirmed_by: str = HOST_SOUL_HTTP_API,
) -> dict[str, Any]:
    source_type, source_id = parse_candidate_id(candidate_id)
    if source_type == "patch":
        proposal = find_patch_proposal(source_id, project_dir)
        confirmed = confirmed_review_proposal(proposal)
        next_state = apply_confirmed_proposal(project_dir, confirmed, confirmed_by=confirmed_by)
        return {"candidate_id": candidate_id, "result": {"state": next_state, "applied_patch": confirmed}}
    if source_type == "working":
        promoted = promote_working_item(project_dir, source_id, confirmed_by=confirmed_by)
        proposal = confirmed_review_proposal(promoted["patch_proposal"])
        next_state = apply_confirmed_proposal(project_dir, proposal, confirmed_by=confirmed_by)
        return {"candidate_id": candidate_id, "result": {"working_item": promoted["working_item"], "state": next_state}}
    raise ValueError(f"Unsupported review candidate type: {source_type}")


def reject_review_candidate(
    project_dir: Path,
    candidate_id: str,
    *,
    reason: str = "",
    rejected_by: str = HOST_SOUL_HTTP_API,
) -> dict[str, Any]:
    source_type, source_id = parse_candidate_id(candidate_id)
    if source_type == "patch":
        proposal = find_patch_proposal(source_id, project_dir)
        record = append_patch_status(
            proposal,
            PATCH_STATUS_REJECTED,
            project_dir,
            reason=reason,
            updated_by=rejected_by,
        )
        return {"candidate_id": candidate_id, "rejected": record}
    if source_type == "working":
        return {"candidate_id": candidate_id, "rejected": reject_working_item(project_dir, source_id, reason=reason)}
    raise ValueError(f"Unsupported review candidate type: {source_type}")


def edit_review_candidate(project_dir: Path, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_type, source_id = parse_candidate_id(candidate_id)
    statement = optional_str(payload.get("statement"))
    reason = optional_str(payload.get("reason"))
    scope = optional_str(payload.get("scope"))
    updated_by = str(payload.get("updated_by") or HOST_SOUL_HTTP_API)
    if source_type == "patch":
        proposal = find_patch_proposal(source_id, project_dir)
        knowledge_points = None
        if statement:
            knowledge_points = [
                {
                    "statement": statement,
                    "kind": str(payload.get("kind") or "accepted_belief"),
                    "priority": str(payload.get("priority") or "medium"),
                    "confidence": float(payload.get("confidence") or 0.75),
                    "why_remember": reason or "",
                }
            ]
        edited = edit_patch_proposal(
            proposal,
            project_dir,
            title=scope,
            why_remember=reason,
            knowledge_points=knowledge_points,
            updated_by=updated_by,
        )
        return {"candidate_id": candidate_id, "edited": edited}
    if source_type == "working":
        edited = edit_working_item(
            project_dir,
            source_id,
            statement=statement,
            reason=reason,
            scope=scope,
            review_after=optional_str(payload.get("review_after")),
            expires_at=optional_str(payload.get("expires_at")),
            updated_by=updated_by,
        )
        return {"candidate_id": candidate_id, "edited": edited}
    raise ValueError(f"Unsupported review candidate type: {source_type}")


def snooze_review_candidate(project_dir: Path, candidate_id: str, *, hours: int = 24) -> dict[str, Any]:
    source_type, source_id = parse_candidate_id(candidate_id)
    if source_type != "working":
        raise ValueError("Only Working State review candidates can be snoozed.")
    until = datetime.now().astimezone() + timedelta(hours=hours)
    edited = edit_working_item(
        project_dir,
        source_id,
        review_after=until.isoformat(),
        expires_at=until.isoformat(),
        updated_by=HOST_SOUL_HTTP_API,
    )
    return {"candidate_id": candidate_id, "snoozed": edited}


def apply_confirmed_proposal(project_dir: Path, proposal: PatchProposal, *, confirmed_by: str) -> StateDoc:
    state = load_state(project_dir)
    next_state = apply_patch_proposal(state, proposal, confirmed_by=confirmed_by)
    save_state(next_state, project_dir)
    append_patch_status(proposal, PATCH_STATUS_APPLIED, project_dir, updated_by=confirmed_by)
    return next_state


def confirmed_review_proposal(proposal: PatchProposal) -> PatchProposal:
    confirmed = json.loads(json.dumps(proposal, ensure_ascii=False))
    for point in confirmed.get("knowledge_points", []):
        if isinstance(point, dict):
            point["status"] = PATCH_STATUS_ACCEPTED
    for operation in confirmed.get("operations", []):
        value = operation.get("value") if isinstance(operation, dict) else None
        if isinstance(value, dict):
            value["status"] = PATCH_STATUS_ACCEPTED
    return confirmed


def optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def parse_candidate_id(candidate_id: str) -> tuple[str, str]:
    if ":" not in candidate_id:
        raise ValueError(f"Invalid review candidate id: {candidate_id}")
    source_type, source_id = candidate_id.split(":", 1)
    if not source_id:
        raise ValueError(f"Invalid review candidate id: {candidate_id}")
    return source_type, source_id
