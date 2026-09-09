from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from soul.services.shared.constants import (
    PATCH_STATUS_APPLIED,
    PATCH_STATUS_PROPOSED,
    PATCH_STATUS_REJECTED,
    WORKING_STATUS_CONFLICT_NEEDS_REVIEW,
    WORKING_STATUS_WORKING,
)
from soul.services.shared.state_types import PatchProposal
from soul.services.integrations.queue import queue_status
from soul.services.state_core.state_store import load_patch_proposals, load_state, state_paths
from soul.services.state_core.working_state import load_working_state


def audit_state(project_dir: Path | None = None) -> dict[str, Any]:
    project = project_dir or Path.cwd()
    issues: list[dict[str, Any]] = []
    state = load_state(project, project_name=project.name)
    working = load_working_state(project, project_name=project.name)
    proposals = load_patch_proposals(project)
    latest = latest_patch_records(proposals)
    paths = state_paths(project)
    queue = queue_status(project)

    expected_version = 1 + len(state.get("history", []))
    if state.get("version") != expected_version:
        issues.append(
            {
                "severity": "warning",
                "code": "state_version_history_mismatch",
                "message": f"state version {state.get('version')} does not match 1 + history length {expected_version}.",
            }
        )

    if not paths.state_markdown_path.exists():
        issues.append({"severity": "warning", "code": "missing_state_markdown", "message": "STATE.md is missing."})

    applied_patch_ids = {str(entry.get("proposal_id") or "") for entry in state.get("history", [])}
    latest_status = {proposal_id: str(proposal.get("status") or "") for proposal_id, proposal in latest.items()}
    for proposal_id in sorted(applied_patch_ids):
        if proposal_id and latest_status.get(proposal_id) not in {PATCH_STATUS_APPLIED, ""}:
            issues.append(
                {
                    "severity": "warning",
                    "code": "history_patch_not_marked_applied",
                    "message": f"history references {proposal_id}, but latest patch log status is {latest_status.get(proposal_id)}.",
                    "proposal_id": proposal_id,
                }
            )

    for item in state.get("current_state", {}).get("state_items", []):
        status = str(item.get("status") or "")
        if status in {"needs_review", "tentative"}:
            issues.append(
                {
                    "severity": "warning",
                    "code": "review_status_in_accepted_state",
                    "message": "Current State contains an item that still has a review-only status.",
                    "item_id": item.get("id", ""),
                    "status": status,
                }
            )

    if queue.blocked:
        issues.append(
            {
                "severity": "warning",
                "code": "queue_blocked",
                "message": f"{queue.blocked} queue job(s) are blocked and will not be retried automatically.",
                "action": "Fix the root cause, then run `soul queue retry-blocked --project-dir .`.",
            }
        )
    if queue.failed_retryable:
        issues.append(
            {
                "severity": "warning",
                "code": "queue_retry_pending",
                "message": f"{queue.failed_retryable} queue job(s) are waiting for retry.",
            }
        )
    if queue.last_error:
        issues.append(
            {
                "severity": "info",
                "code": "queue_last_error",
                "message": str(queue.last_error.get("error") or "unknown"),
                "blocked_reason": queue.last_error.get("blocked_reason"),
            }
        )

    working_items = working.get("items", [])
    active_working = [
        item
        for item in working_items
        if item.get("status") in {WORKING_STATUS_WORKING, WORKING_STATUS_CONFLICT_NEEDS_REVIEW}
    ]
    proposed = [proposal for proposal in latest.values() if proposal.get("status") == PATCH_STATUS_PROPOSED]
    applied = [proposal for proposal in latest.values() if proposal.get("status") == PATCH_STATUS_APPLIED]
    rejected = [proposal for proposal in latest.values() if proposal.get("status") == PATCH_STATUS_REJECTED]

    return {
        "ok": not any(issue.get("severity") == "error" for issue in issues),
        "project": state.get("project", project.name),
        "project_dir": str(project),
        "summary": {
            "state_version": state.get("version"),
            "history_entries": len(state.get("history", [])),
            "state_items": len(state.get("current_state", {}).get("state_items", [])),
            "working_items": len(working_items),
            "active_working_items": len(active_working),
            "patch_records": len(proposals),
            "latest_patch_proposed": len(proposed),
            "latest_patch_applied": len(applied),
            "latest_patch_rejected": len(rejected),
            "queue_queued": queue.queued,
            "queue_processing": queue.started,
            "queue_failed_retryable": queue.failed_retryable,
            "queue_blocked": queue.blocked,
            "queue_dead_letter": queue.dead_letter,
        },
        "issues": issues,
    }


def latest_patch_records(proposals: Sequence[PatchProposal]) -> dict[str, PatchProposal]:
    latest: dict[str, PatchProposal] = {}
    for proposal in proposals:
        proposal_id = str(proposal.get("id") or "")
        if proposal_id:
            latest[proposal_id] = proposal
    return latest
