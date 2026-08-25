from __future__ import annotations

from datetime import UTC, datetime, timedelta

from soul.services.shared.constants import PATCH_STATUS_REJECTED, WORKING_STATUS_CONFLICT_NEEDS_REVIEW
from soul.services.state_core.proposals import append_patch_status, propose_patch
from soul.services.state_core.review.card import build_review_card
from soul.services.state_core.state_store import append_patch_proposal, load_state
from soul.services.state_core.working_state import edit_working_item, upsert_working_state_from_evidence


def evidence_refs() -> list[dict[str, object]]:
    return [{"type": "reme_file", "path": "daily/2026-08-22/review.md", "start_line": 1, "end_line": 3}]


def test_review_card_shows_auto_accept_patch_as_ready(tmp_path):
    state = load_state(tmp_path, project_name="Review Card Test")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "Use pnpm in this project.",
            "evidence_refs": evidence_refs(),
            "state_item": {
                "id": "prefer-pnpm",
                "kind": "active_constraint",
                "statement": "Use pnpm for dependency commands in this project.",
                "priority": "high",
                "confidence": 0.9,
            },
        },
    )
    append_patch_proposal(proposal, tmp_path)

    card = build_review_card(tmp_path)

    assert card["counts"]["ready_to_confirm"] == 1
    candidate = card["ready_to_confirm"][0]
    assert candidate["source_type"] == "patch_proposal"
    assert candidate["recommended_action"] == "accept"
    assert "pnpm" in candidate["statement"]
    assert candidate["evidence"]["refs"] == evidence_refs()


def test_review_card_ignores_rejected_patch_latest_status(tmp_path):
    state = load_state(tmp_path, project_name="Review Card Test")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "Use pnpm in this project.",
            "state_item": {
                "id": "prefer-pnpm",
                "kind": "active_constraint",
                "statement": "Use pnpm for dependency commands in this project.",
            },
        },
    )
    append_patch_proposal(proposal, tmp_path)
    append_patch_status(proposal, PATCH_STATUS_REJECTED, tmp_path, reason="not true", updated_by="test")

    card = build_review_card(tmp_path)

    assert card["has_reviewable_content"] is False
    assert card["counts"]["total"] == 0


def test_review_card_splits_marked_due_and_conflict_working_state(tmp_path):
    state = load_state(tmp_path, project_name="Review Card Test")
    due = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "dependency setup",
            "summary": "后续默认使用 pnpm。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "后续默认使用 pnpm。",
                "reason": "用户已经明确纠正包管理器。",
                "scope": "dependency setup",
                "review_after": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "review_card": True,
            },
        },
        state=state,
    )
    conflict = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "model direction",
            "summary": "当前主线不再是 ConvLSTM loss 调参。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "当前主线不再是 ConvLSTM loss 调参。",
                "reason": "新证据和已确认状态冲突。",
                "scope": "model direction",
            },
        },
        state={
            **state,
            "current_state": {
                **state["current_state"],
                "state_items": [
                    {
                        "id": "prefer-npm",
                        "kind": "active_constraint",
                        "statement": "当前主线暂定为 ConvLSTM loss 调参。",
                    }
                ],
            },
        },
    )

    card = build_review_card(tmp_path)

    ready_ids = {candidate["source_id"] for candidate in card["ready_to_confirm"]}
    review_ids = {candidate["source_id"] for candidate in card["needs_review"]}
    assert due["item"]["id"] in ready_ids
    assert conflict["item"]["id"] in review_ids
    assert conflict["item"]["status"] == WORKING_STATUS_CONFLICT_NEEDS_REVIEW


def test_review_card_shows_due_working_state_as_needs_review(tmp_path):
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "Review Card UI",
            "summary": "后续回答默认把 Review Card 当成低打扰确认队列。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "后续回答默认把 Review Card 当成低打扰确认队列。",
                "reason": "产品定义复述，不需要提醒。",
                "scope": "Soul Review Card UX",
                "review_after": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )

    card = build_review_card(tmp_path)

    assert card["counts"]["needs_review"] == 1
    assert card["needs_review"][0]["source_id"] == result["item"]["id"]
    assert card["needs_review"][0]["recommended_action"] == "review"


def test_review_card_filters_non_review_candidate_working_state(tmp_path):
    upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "Review Card UI",
            "summary": "后续回答默认把 Review Card 当成低打扰确认队列。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "后续回答默认把 Review Card 当成低打扰确认队列。",
                "reason": "产品定义复述，不需要提醒。",
                "scope": "Soul Review Card UX",
                "review_after": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "review_candidate": False,
            },
        },
    )

    card = build_review_card(tmp_path)

    assert card["has_reviewable_content"] is False


def test_edit_working_item_updates_fields_and_keeps_reviewable(tmp_path):
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "dependency setup",
            "summary": "后续默认使用 npm。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "后续默认使用 npm。",
                "reason": "旧判断。",
                "scope": "dependency setup",
                "review_card": True,
            },
        },
    )

    edited = edit_working_item(
        tmp_path,
        result["item"]["id"],
        statement="后续默认使用 pnpm。",
        reason="用户明确纠正为 pnpm。",
        review_after=(datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    )
    card = build_review_card(tmp_path)

    assert edited.get("statement") == "后续默认使用 pnpm。"
    assert edited.get("reason") == "用户明确纠正为 pnpm。"
    assert card["ready_to_confirm"][0]["source_id"] == result["item"]["id"]
    assert "pnpm" in card["ready_to_confirm"][0]["statement"]
