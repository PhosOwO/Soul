from __future__ import annotations

import json
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen
from urllib.request import Request

import pytest

from soul.api import SoulApi, make_handler
from soul.adapters.reme import ReMeJobResult
from soul.services.project_resolver import project_id_for_path, register_project
from soul.services.state_core.proposals import propose_patch
from soul.services.state_core.state_store import append_patch_proposal, load_patch_proposals
from soul.services.state import load_state
from soul.services.state_core.working_state import load_working_state, upsert_working_state_from_evidence


def datetime_suffix() -> str:
    return "-" + datetime.now(UTC).strftime("%Y%m%d")


def test_soul_api_get_state_returns_injection(tmp_path):
    load_state(tmp_path, project_name="Demo")

    payload = SoulApi(tmp_path).get_state(task="下一步怎么做？", source="deepseek-harness")

    assert "Soul Current State" in payload["context"]
    assert "[Soul Current State]" in payload["injection"]
    assert payload["task"] == "下一步怎么做？"
    runs = (tmp_path / ".soul" / "state" / "integration_runs.jsonl").read_text(encoding="utf-8")
    assert '"host": "deepseek-harness"' in runs
    assert '"operation": "get_state"' in runs


def test_soul_api_get_state_includes_unconfirmed_working_state(tmp_path):
    load_state(tmp_path, project_name="Demo")
    upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "MHW 漏报诊断",
            "summary": "当前主线暂定为 SST-first Qnet/STR。",
            "evidence_refs": [{"type": "reme_file", "path": "daily/2026-08-20/mhw.md"}],
            "working_state": {
                "route": "working_state",
                "statement": "当前主线暂定为 SST-first Qnet/STR。",
                "reason": "不保留会导致下一轮继续重复旧方向。",
                "scope": "MHW 漏报诊断",
            },
        },
    )

    payload = SoulApi(tmp_path).get_state(task="继续 MHW 漏报诊断")

    assert "Use Accepted State as confirmed project cognition" in payload["injection"]
    assert "Working State, unconfirmed:" in payload["injection"]
    assert "SST-first Qnet/STR" in payload["injection"]


def test_soul_api_review_page_and_card_endpoint(tmp_path):
    load_state(tmp_path, project_name="Demo")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(SoulApi(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        review = urlopen(base + "/review", timeout=5).read().decode("utf-8")
        card = json.loads(urlopen(base + "/review-card", timeout=5).read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert "Soul Review" in review
    assert "Ready to Confirm" in review
    assert card["has_reviewable_content"] is False


def test_soul_api_global_review_index_and_project_card(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "repo"
    project.mkdir()
    state = load_state(project, project_name="Repo")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "Use uv for Python commands.",
            "state_item": {
                "id": "prefer-uv",
                "kind": "active_constraint",
                "statement": "Use uv for Python commands in this project.",
            },
        },
    )
    append_patch_proposal(proposal, project)
    record = register_project(project, project_name="Repo")

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(SoulApi(tmp_path, register=False)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        index = json.loads(urlopen(base + "/review-index?scan=1", timeout=5).read().decode("utf-8"))
        card = json.loads(
            urlopen(base + f"/review-card?project_id={record['project_id']}", timeout=5).read().decode("utf-8")
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert index["projects"][0]["project_id"] == record["project_id"]
    assert index["projects"][0]["review"]["ready_to_confirm"] == 1
    assert card["project_id"] == project_id_for_path(project)
    assert card["project"] == "Repo"
    assert card["project_dir"] == str(project.resolve())
    assert card["counts"]["ready_to_confirm"] == 1


def test_soul_api_global_review_action_routes_by_project_id(tmp_path, monkeypatch):
    soul_home = tmp_path / "soul-home"
    monkeypatch.setenv("SOUL_HOME", str(soul_home))
    project = tmp_path / "repo"
    project.mkdir()
    state = load_state(project, project_name="Repo")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "Use pnpm for frontend commands.",
            "state_item": {
                "id": "prefer-pnpm",
                "kind": "active_constraint",
                "statement": "Use pnpm for frontend commands in this project.",
            },
        },
    )
    append_patch_proposal(proposal, project)
    record = register_project(project, project_name="Repo")

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(SoulApi(tmp_path, register=False)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        request = Request(
            base + "/review/accept",
            data=json.dumps(
                {
                    "project_id": record["project_id"],
                    "candidate_id": f"patch:{proposal['id']}",
                    "confirmed_by": "test",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        accepted = json.loads(urlopen(request, timeout=5).read().decode("utf-8"))
        index = json.loads(urlopen(base + "/review-index", timeout=5).read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert accepted["result"]["state"]["version"] == 2
    assert load_state(project)["version"] == 2
    assert index["projects"][0]["review"]["total"] == 0


def test_soul_api_review_accept_patch_applies_and_removes_candidate(tmp_path):
    state = load_state(tmp_path, project_name="Demo")
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
    api = SoulApi(tmp_path)

    assert api.review_card()["counts"]["ready_to_confirm"] == 1
    result = api.accept_review_candidate(f"patch:{proposal['id']}", confirmed_by="test")

    assert result["result"]["state"]["version"] == 2
    accepted = next(item for item in result["result"]["state"]["current_state"]["state_items"] if item["id"] == "prefer-pnpm")
    assert accepted["status"] == "accepted"
    assert api.review_card()["has_reviewable_content"] is False
    assert load_patch_proposals(tmp_path)[-1]["status"] == "applied"


def test_soul_api_review_accept_working_state_writes_accepted_state_item(tmp_path):
    load_state(tmp_path, project_name="Demo")
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "Review Card UI",
            "summary": "当前不再把 Review Card 设计成完整 project state dashboard。",
            "evidence_refs": [{"type": "reme_file", "path": "daily/2026-08-22/review.md"}],
            "working_state": {
                "route": "working_state",
                "statement": "当前不再把 Review Card 设计成完整 project state dashboard。",
                "reason": "用户确认低打扰确认队列才是目标。",
                "scope": "Review Card UI",
                "review_after": "2000-01-01T00:00:00Z",
                "review_card": True,
            },
        },
    )
    api = SoulApi(tmp_path)

    accepted = api.accept_review_candidate(f"working:{result['item']['id']}", confirmed_by="test")

    item = next(
        state_item
        for state_item in accepted["result"]["state"]["current_state"]["state_items"]
        if state_item["statement"] == "当前不再把 Review Card 设计成完整 project state dashboard。"
    )
    assert item["status"] == "accepted"


def test_soul_api_review_edit_snooze_and_reject_working_state(tmp_path):
    load_state(tmp_path, project_name="Demo")
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "dependency setup",
            "summary": "后续默认使用 npm。",
            "evidence_refs": [{"type": "reme_file", "path": "daily/2026-08-22/review.md"}],
            "working_state": {
                "route": "working_state",
                "statement": "后续默认使用 npm。",
                "reason": "旧判断。",
                "scope": "dependency setup",
                "review_after": "2000-01-01T00:00:00Z",
                "expires": "2099-01-01T00:00:00Z",
                "review_card": True,
            },
        },
    )
    api = SoulApi(tmp_path)
    candidate_id = f"working:{result['item']['id']}"

    edited = api.edit_review_candidate(
        candidate_id,
        {"statement": "后续默认使用 pnpm。", "reason": "用户纠正为 pnpm。", "scope": "dependency setup"},
    )
    assert edited["edited"]["statement"] == "后续默认使用 pnpm。"
    assert api.review_card()["counts"]["ready_to_confirm"] == 1
    expires_at = edited["edited"]["expires_at"]

    snoozed = api.snooze_review_candidate(candidate_id, hours=24)
    assert api.review_card()["has_reviewable_content"] is False
    assert snoozed["snoozed"]["expires_at"] == expires_at
    assert snoozed["snoozed"]["review_after"] != "2000-01-01T00:00:00Z"

    rejected = api.reject_review_candidate(candidate_id, reason="not durable", rejected_by="test")
    assert rejected["rejected"]["status"] == "rejected"


def test_soul_api_review_expire_and_extend_working_state(tmp_path):
    load_state(tmp_path, project_name="Demo")
    result = upsert_working_state_from_evidence(
        tmp_path,
        {
            "source": "test",
            "task": "lifecycle",
            "summary": "后续默认保留短期上下文。",
            "evidence_refs": [{"type": "reme_file", "path": "daily/2026-08-22/lifecycle.md"}],
            "working_state": {
                "route": "working_state",
                "statement": "后续默认保留短期上下文。",
                "reason": "用户需要跨 turn 保留。",
                "scope": "lifecycle",
                "review_after": "2000-01-01T00:00:00Z",
                "expires": "2000-01-01T00:00:00Z",
            },
        },
    )
    api = SoulApi(tmp_path)
    candidate_id = f"working:{result['item']['id']}"

    extended = api.extend_review_candidate(candidate_id, hours=24)

    assert extended["extended"]["expires_at"] != "2000-01-01T00:00:00Z"
    assert extended["extended"]["review_after"] == "2000-01-01T00:00:00Z"

    expired = api.expire_review_candidate(candidate_id, reason="stale")
    assert expired["expired"]["status"] == "expired"
    assert load_working_state(tmp_path)["items"][0]["status"] == "expired"


def test_soul_api_enqueue_evidence_records_non_blocking_job(tmp_path, monkeypatch):
    load_state(tmp_path, project_name="Demo")
    monkeypatch.setenv("SOUL_DISABLE_BACKGROUND_DRAIN", "1")

    payload = SoulApi(tmp_path).enqueue_evidence(
        evidence={
            "source": "deepseek-harness",
            "task": "Capture this turn",
            "outcome": "Queue it for ReMe processing.",
            "session_id": "dsh-session-1",
            "turn_id": "turn-1",
        },
        reme={"search_limit": 2},
    )

    assert payload["memory_mode"] == "soul_reme"
    assert payload["queued"] is True
    assert payload["session_id"] == "dsh-session-1"
    assert payload["turn_id"] == "turn-1"
    assert payload["background_drain_started"] is False
    assert not (tmp_path / ".soul" / "state" / "patch_proposals.jsonl").exists()
    jobs = (tmp_path / ".soul" / "state" / "queue" / "jobs.jsonl").read_text(encoding="utf-8")
    assert '"source": "deepseek-harness"' in jobs
    assert '"search_limit": 2' in jobs
    runs = (tmp_path / ".soul" / "state" / "integration_runs.jsonl").read_text(encoding="utf-8")
    assert '"operation": "enqueue_evidence"' in runs
    assert '"host": "deepseek-harness"' in runs


def test_soul_api_enqueue_evidence_filters_reme_write_controls(tmp_path, monkeypatch):
    load_state(tmp_path, project_name="Demo")
    monkeypatch.setenv("SOUL_DISABLE_BACKGROUND_DRAIN", "1")

    SoulApi(tmp_path).enqueue_evidence(
        evidence={
            "source": "deepseek-harness",
            "task": "Capture this turn",
            "outcome": "Queue it for ReMe processing.",
            "session_id": "dsh-session-1",
            "turn_id": "turn-1",
        },
        reme={
            "search_limit": 2,
            "date": "2026-08-19",
            "memory_hint": "Keep durable project knowledge.",
            "write_mode": "fallback_daily_write",
            "workspace_dir": "/tmp/not-soul-reme",
        },
    )

    jobs = (tmp_path / ".soul" / "state" / "queue" / "jobs.jsonl").read_text(encoding="utf-8")
    job = json.loads(jobs.splitlines()[-1])

    assert job["payload"]["reme"] == {
        "search_limit": 2,
        "date": "2026-08-19",
        "memory_hint": "Keep durable project knowledge.",
    }


def test_soul_api_enqueue_evidence_uses_project_scoped_fallback_session(tmp_path, monkeypatch):
    load_state(tmp_path, project_name="Demo")
    monkeypatch.setenv("SOUL_DISABLE_BACKGROUND_DRAIN", "1")

    payload = SoulApi(tmp_path).enqueue_evidence(
        evidence={
            "source": "deepseek-harness",
            "task": "Capture this turn",
            "outcome": "Queue it for ReMe processing.",
            "turn_id": "turn-1",
        },
    )

    assert payload["session_id"].startswith("soul-deepseek-harness-")
    assert payload["session_id"].endswith(datetime_suffix())
    assert payload["session_id"] != "deepseek-harness-session"


def test_soul_api_reme_transition_writes_reme_and_proposes_refs_only_patch(tmp_path, monkeypatch):
    load_state(tmp_path, project_name="Demo")

    captured_auto_memory = {}

    class FakeReMeAdapter:
        def __init__(self, project_dir, workspace_dir=None):
            self.workspace_dir = workspace_dir

        def auto_memory(self, **kwargs):
            captured_auto_memory.update(kwargs)
            return ReMeJobResult(
                job="auto_memory",
                command=["reme", "auto_memory"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={
                    "path": "daily/2026-08-16/dsh_demo.md",
                    "source_conversation": "session/dialog/session-1.jsonl",
                },
            )

        def search(self, **kwargs):
            return ReMeJobResult(
                job="search",
                command=["reme", "start", "job=search"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={
                    "counts": {"vector": 0, "keyword": 1, "returned": 1},
                    "results": [
                        {
                            "id": "chunk-1",
                            "path": "daily\\2026-08-16\\dsh_demo.md",
                            "start_line": 1,
                            "end_line": 12,
                            "scores": {"score": 2.5},
                        }
                    ],
                },
            )

    monkeypatch.setattr("soul.api.ReMeCliAdapter", FakeReMeAdapter)

    payload = SoulApi(tmp_path).propose_reme_transition(
        evidence={"task": "Recall 还是不够，下一步怎么办？", "outcome": "先验证 MLD。"},
        episode={"events": [{"type": "turn/end"}], "session_id": "session-1"},
        reme={"search_limit": 3},
    )

    assert payload["memory_mode"] == "soul_reme"
    assert payload["reme_write_mode"] == "auto_memory"
    assert captured_auto_memory["messages"] == [
        {"name": "user", "role": "user", "content": "Recall 还是不够，下一步怎么办？"},
        {"name": "assistant", "role": "assistant", "content": "先验证 MLD。"},
    ]
    assert payload["evidence_refs"] == [
        {
            "type": "reme_file_chunk",
            "path": "daily/2026-08-16/dsh_demo.md",
            "chunk_id": "chunk-1",
            "start_line": 1,
            "end_line": 12,
            "score": 2.5,
        },
        {"type": "reme_file", "path": "daily/2026-08-16/dsh_demo.md"},
        {"type": "reme_file", "path": "session/dialog/session-1.jsonl"},
    ]
    assert payload["working_state"]["route"] == "working_state"
    assert payload["working_state"]["item"]["statement"] == "先验证 MLD。"
    assert not (tmp_path / ".soul" / "state" / "patch_proposals.jsonl").exists()
    assert payload["trace_path"] == ".soul/traces/reme_state_trace.md"
    trace = (tmp_path / ".soul" / "traces" / "reme_state_trace.md").read_text(encoding="utf-8")
    assert "reme://daily/2026-08-16/dsh_demo.md:1-12#chunk-1" in trace
    assert "working_state_route: working_state" in trace
    runs = (tmp_path / ".soul" / "state" / "integration_runs.jsonl").read_text(encoding="utf-8")
    assert '"operation": "propose_reme_transition"' in runs
    assert '"memory_mode": "soul_reme"' in runs


def test_soul_api_reme_transition_ignores_external_workspace_for_writes(tmp_path, monkeypatch):
    captured_workspace = None

    class FakeReMeAdapter:
        def __init__(self, project_dir, workspace_dir=None):
            nonlocal captured_workspace
            captured_workspace = workspace_dir
            self.workspace_dir = workspace_dir

        def daily_write(self, **kwargs):
            return ReMeJobResult(
                job="daily_write",
                command=["reme", "start", "job=daily_write"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={"path": "daily/2026-08-18/fallback.md"},
            )

        def search(self, **kwargs):
            return ReMeJobResult(
                job="search",
                command=["reme", "start", "job=search"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={"counts": {"returned": 0}, "results": []},
            )

    monkeypatch.setattr("soul.api.ReMeCliAdapter", FakeReMeAdapter)

    payload = SoulApi(tmp_path).propose_reme_transition(
        evidence={"task": "Keep workspace local", "summary": "Do not write daily at project root"},
        reme={"write_mode": "fallback_daily_write", "workspace_dir": str(tmp_path)},
    )

    assert captured_workspace == tmp_path / ".soul" / "reme"
    assert payload["working_state"]["route"] == "working_state"


def test_soul_api_reme_transition_can_use_fallback_daily_write(tmp_path, monkeypatch):
    load_state(tmp_path, project_name="Demo")

    class FakeReMeAdapter:
        def __init__(self, project_dir, workspace_dir=None):
            self.workspace_dir = workspace_dir

        def daily_write(self, **kwargs):
            return ReMeJobResult(
                job="daily_write",
                command=["reme", "start", "job=daily_write"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={"path": "daily/2026-08-16/fallback.md"},
            )

        def search(self, **kwargs):
            return ReMeJobResult(
                job="search",
                command=["reme", "search"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={"counts": {"returned": 0}, "results": []},
            )

    monkeypatch.setattr("soul.api.ReMeCliAdapter", FakeReMeAdapter)

    payload = SoulApi(tmp_path).propose_reme_transition(
        evidence={"task": "Fallback", "summary": "Use fallback"},
        reme={"write_mode": "fallback_daily_write"},
    )

    assert payload["reme_write_mode"] == "fallback_daily_write"
    assert {"type": "reme_file", "path": "daily/2026-08-16/fallback.md"} in payload["evidence_refs"]


def test_soul_api_reme_transition_does_not_implicitly_fallback_when_auto_memory_needs_credentials(
    tmp_path, monkeypatch
):
    load_state(tmp_path, project_name="Demo")

    calls = []

    class FakeReMeAdapter:
        def __init__(self, project_dir, workspace_dir=None):
            self.workspace_dir = workspace_dir

        def auto_memory(self, **kwargs):
            calls.append(("auto_memory", kwargs))
            raise RuntimeError("Missing credentials. Set OPENAI_API_KEY.")

        def daily_write(self, **kwargs):
            calls.append(("daily_write", kwargs))
            return ReMeJobResult(
                job="daily_write",
                command=["reme", "start", "job=daily_write"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={"path": "daily/2026-08-17/fallback.md"},
            )

        def search(self, **kwargs):
            calls.append(("search", kwargs))
            return ReMeJobResult(
                job="search",
                command=["reme", "start", "job=search"],
                returncode=0,
                stdout="",
                stderr="",
                answer="",
                metadata={"counts": {"returned": 0}, "results": []},
            )

    monkeypatch.setattr("soul.api.ReMeCliAdapter", FakeReMeAdapter)

    with pytest.raises(RuntimeError, match="Missing credentials"):
        SoulApi(tmp_path).propose_reme_transition(
            evidence={"task": "Fallback from auto_memory", "summary": "Use daily write"},
            reme={"write_mode": "auto_memory"},
        )

    assert [call[0] for call in calls] == ["auto_memory"]
    assert not (tmp_path / ".soul" / "state" / "patch_proposals.jsonl").exists()


def test_soul_api_reme_read_trace_consolidate_and_proactive(tmp_path, monkeypatch):
    load_state(tmp_path, project_name="Demo")

    calls = []

    class FakeReMeAdapter:
        def __init__(self, project_dir, workspace_dir=None):
            self.workspace_dir = workspace_dir

        def read(self, **kwargs):
            calls.append(("read", kwargs))
            return ReMeJobResult("read", ["reme", "read"], 0, "", "", "read answer", {"path": kwargs["path"]})

        def traverse(self, **kwargs):
            calls.append(("traverse", kwargs))
            return ReMeJobResult("traverse", ["reme", "traverse"], 0, "", "", "trace answer", {"path": kwargs["path"]})

        def auto_dream(self, **kwargs):
            calls.append(("auto_dream", kwargs))
            return ReMeJobResult("auto_dream", ["reme", "auto_dream"], 0, "", "", "dream answer", {"integrated": 1})

        def proactive(self, **kwargs):
            calls.append(("proactive", kwargs))
            return ReMeJobResult("proactive", ["reme", "proactive"], 0, "", "", "topic answer", {"topics": []})

    monkeypatch.setattr("soul.api.ReMeCliAdapter", FakeReMeAdapter)
    api = SoulApi(tmp_path)

    assert api.read_evidence("daily/a.md", start_line=1, end_line=2)["answer"] == "read answer"
    assert api.trace_evidence("digest/a.md", depth=2, direction="both")["answer"] == "trace answer"
    assert api.consolidate_memory(date="2026-08-17", hint="h", scan_days=2, max_units=3)["answer"] == "dream answer"
    assert api.get_proactive_topics(date="2026-08-17")["answer"] == "topic answer"
    assert calls == [
        ("read", {"path": "daily/a.md", "start_line": 1, "end_line": 2}),
        ("traverse", {"path": "digest/a.md", "depth": 2, "direction": "both"}),
        ("auto_dream", {"date": "2026-08-17", "hint": "h", "scan_days": 2, "max_units": 3}),
        ("proactive", {"date": "2026-08-17", "include_content": False}),
    ]
