from __future__ import annotations

from datetime import UTC, datetime, timedelta

from soul.services.shared.constants import (
    WORKING_STATUS_CONFLICT_NEEDS_REVIEW,
    WORKING_STATUS_PROMOTED,
    WORKING_STATUS_WORKING,
)
from soul.services.state_core.proposals import apply_patch_proposal, propose_patch
from soul.services.state_core.state_store import load_patch_proposals, load_state, load_state_markdown
from soul.services.state_core.working_state import (
    load_working_state,
    project_working_state_items,
    promote_working_item,
    review_working_items,
    upsert_working_state_from_evidence,
)


def evidence_refs() -> list[dict[str, object]]:
    return [{"type": "reme_file", "path": "daily/2026-08-20/mhw.md"}]


def test_episode_summary_does_not_enter_working_state(tmp_path):
    state = load_state(tmp_path, project_name="Working Test")
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "MHW notes",
            "summary": "今天阅读了 SST anomaly 笔记。",
            "evidence_refs": evidence_refs(),
        },
        state=state,
    )

    assert result["route"] == "no_state"
    assert load_working_state(tmp_path)["items"] == []


def test_loading_empty_working_state_does_not_create_snapshot(tmp_path):
    doc = load_working_state(tmp_path, project_name="Working Test")

    assert doc["items"] == []
    assert not (tmp_path / ".soul" / "state" / "working_state.json").exists()


def test_cognitive_diff_enters_working_state_and_context(tmp_path):
    state = load_state(tmp_path, project_name="Working Test")
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "MHW 漏报诊断",
            "summary": "阅读 MHW 笔记",
            "content": (
                "当前 MHW 漏报已诊断出集中在近阈值、短持续、onset、小尺度、高等级和 "
                "coastal/boundary 机制异质事件。当前主线应从继续调 ConvLSTM loss 转向 "
                "SST-first Qnet/STR，再考虑 MLD、SSH/current。"
            ),
            "evidence_refs": evidence_refs(),
        },
        state=state,
    )

    assert result["route"] == "working_state"
    item = result["item"]
    assert item["status"] == WORKING_STATUS_WORKING
    assert item["review_candidate"] is True
    assert "SST-first Qnet/STR" in item["statement"]
    assert item["review_after"].endswith("Z")

    context = load_state_markdown(tmp_path, task="继续 MHW 漏报诊断")
    assert "Working State, unconfirmed:" in context
    assert "SST-first Qnet/STR" in context
    assert "Accepted Beliefs" in context


def test_working_state_requires_evidence_refs(tmp_path):
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "MHW 漏报诊断",
            "content": "当前主线应从继续调 ConvLSTM loss 转向 SST-first Qnet/STR。",
        },
    )

    assert result["route"] == "no_state"
    assert "requires evidence refs" in result["reason"]


def test_expired_working_state_is_not_projected(tmp_path):
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "MHW 漏报诊断",
            "summary": "当前主线暂定为 SST-first Qnet/STR。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "当前主线暂定为 SST-first Qnet/STR。",
                "reason": "不保留会导致下一轮继续重复旧方向。",
                "scope": "MHW 漏报诊断",
                "expires": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )

    assert result["route"] == "working_state"
    assert project_working_state_items(tmp_path, task="MHW 漏报诊断") == []


def test_review_due_working_state_is_marked_in_context(tmp_path):
    upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "MHW 漏报诊断",
            "summary": "当前主线暂定为 SST-first Qnet/STR。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "当前主线暂定为 SST-first Qnet/STR。",
                "reason": "不保留会导致下一轮继续重复旧方向。",
                "scope": "MHW 漏报诊断",
                "review_after": (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )

    context = load_state_markdown(tmp_path, task="继续 MHW 漏报诊断")

    assert "[working, review-due" in context
    assert "Working State Review Due:" in context
    assert "MHW 漏报诊断" in context


def test_promote_working_state_creates_patch_proposal(tmp_path):
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "MHW 漏报诊断",
            "summary": "当前主线暂定为 SST-first Qnet/STR。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "当前主线暂定为 SST-first Qnet/STR。",
                "reason": "不保留会导致下一轮继续重复旧方向。",
                "scope": "MHW 漏报诊断",
            },
        },
    )

    promoted = promote_working_item(tmp_path, result["item"]["id"], confirmed_by="test")

    assert promoted["working_item"]["status"] == WORKING_STATUS_PROMOTED
    proposals = load_patch_proposals(tmp_path)
    assert proposals[-1]["id"] == promoted["patch_proposal"]["id"]
    assert proposals[-1]["review_recommendation"] == "needs_review"
    assert proposals[-1]["knowledge_points"][0]["statement"] == "当前主线暂定为 SST-first Qnet/STR。"


def test_conflicting_working_state_is_reviewable_but_not_projected(tmp_path):
    state = load_state(tmp_path, project_name="Working Test")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "当前主线暂定为 ConvLSTM loss 调参。",
            "state_item": {
                "id": "mhw-current-mainline",
                "kind": "accepted_belief",
                "statement": "当前主线暂定为 ConvLSTM loss 调参。",
                "priority": "high",
                "confidence": 0.9,
            },
        },
    )
    accepted = apply_patch_proposal(state, proposal, confirmed_by="test")

    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "MHW 漏报诊断",
            "summary": "当前主线不再是 ConvLSTM loss 调参。",
            "evidence_refs": evidence_refs(),
            "working_state": {
                "route": "working_state",
                "statement": "当前主线不再是 ConvLSTM loss 调参。",
                "reason": "新证据改变了当前主线。",
                "scope": "MHW 漏报诊断",
            },
        },
        state=accepted,
    )

    assert result["item"]["status"] == WORKING_STATUS_CONFLICT_NEEDS_REVIEW
    assert result["item"]["conflicts_with"] == ["mhw-current-mainline"]
    assert project_working_state_items(tmp_path, task="MHW 漏报诊断") == []
    assert review_working_items(tmp_path)[0].get("id") == result["item"]["id"]
