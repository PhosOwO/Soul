from __future__ import annotations

from datetime import UTC, datetime, timedelta

from soul.services.integrations.queue import (
    EVENT_BLOCKED,
    EVENT_FAILED,
    append_queue_event,
    drain_queue,
    enqueue_turn_evidence,
    queue_status,
    replay_queue_state,
    select_runnable_jobs,
)


def test_queue_selects_fifo_over_runnable_jobs(tmp_path):
    first = enqueue_turn_evidence(
        tmp_path,
        source="test",
        session_id="s1",
        turn_id="t1",
        payload={"task": "first", "outcome": "first outcome"},
    )
    second = enqueue_turn_evidence(
        tmp_path,
        source="test",
        session_id="s1",
        turn_id="t2",
        payload={"task": "second", "outcome": "second outcome"},
    )

    state = replay_queue_state(tmp_path)
    selected = select_runnable_jobs(state, now=datetime.now(UTC), limit=2)

    assert [item.job["job_id"] for item in selected] == [first["job_id"], second["job_id"]]


def test_queue_skips_blocked_jobs_without_blocking_later_jobs(tmp_path):
    first = enqueue_turn_evidence(
        tmp_path,
        source="test",
        session_id="s1",
        turn_id="t1",
        payload={"task": "first", "outcome": "first outcome"},
    )
    second = enqueue_turn_evidence(
        tmp_path,
        source="test",
        session_id="s1",
        turn_id="t2",
        payload={"task": "second", "outcome": "second outcome"},
    )
    append_queue_event(tmp_path, str(first["job_id"]), EVENT_BLOCKED, error="missing credentials")

    state = replay_queue_state(tmp_path)
    selected = select_runnable_jobs(state, now=datetime.now(UTC), limit=2)

    assert [item.job["job_id"] for item in selected] == [second["job_id"]]


def test_queue_retry_waits_for_next_run_at(tmp_path):
    job = enqueue_turn_evidence(
        tmp_path,
        source="test",
        session_id="s1",
        turn_id="t1",
        payload={"task": "first", "outcome": "first outcome"},
    )
    future = (datetime.now(UTC) + timedelta(minutes=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    append_queue_event(
        tmp_path,
        str(job["job_id"]),
        EVENT_FAILED,
        retryable=True,
        attempt=1,
        next_run_at=future,
    )

    state = replay_queue_state(tmp_path)
    selected = select_runnable_jobs(state, now=datetime.now(UTC), limit=1)

    assert selected == []
    summary = queue_status(tmp_path)
    assert summary.failed_retryable == 1


def test_queue_drain_processes_turn_evidence_through_reme(tmp_path, monkeypatch):
    def fake_propose_reme_transition(**kwargs):
        proposals = tmp_path / ".soul" / "state" / "patch_proposals.jsonl"
        proposals.parent.mkdir(parents=True, exist_ok=True)
        proposals.write_text('{"id":"patch_1"}\n', encoding="utf-8")
        return {
            "memory_mode": "soul_reme",
            "reme_write_mode": "auto_memory",
            "patch_proposal": {"id": "patch_1"},
        }

    monkeypatch.setattr("soul.services.integrations.queue.propose_reme_transition", fake_propose_reme_transition)
    enqueue_turn_evidence(
        tmp_path,
        source="codex:mcp",
        session_id="s1",
        turn_id="t1",
        payload={"task": "Remember queue contract", "outcome": "Queue drain calls ReMe."},
    )

    result = drain_queue(tmp_path, limit=1)

    assert result["locked"] is False
    assert result["processed"][0]["status"] == "completed"
    assert (tmp_path / ".soul" / "state" / "patch_proposals.jsonl").exists()
    summary = queue_status(tmp_path)
    assert summary.completed == 1
