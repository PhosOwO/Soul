from __future__ import annotations

from soul.api import SoulApi
from soul.adapters.reme import ReMeJobResult
from soul.services.state import load_state


def test_soul_api_get_state_returns_injection(tmp_path):
    load_state(tmp_path, project_name="Demo")

    payload = SoulApi(tmp_path).get_state(task="下一步怎么做？", source="deepseek-harness")

    assert "Soul Current State" in payload["context"]
    assert "[Soul Current State]" in payload["injection"]
    assert payload["task"] == "下一步怎么做？"
    runs = (tmp_path / ".soul" / "state" / "integration_runs.jsonl").read_text(encoding="utf-8")
    assert '"host": "deepseek-harness"' in runs
    assert '"operation": "get_state"' in runs


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
    assert payload["worker_started"] is False
    assert not (tmp_path / ".soul" / "state" / "patch_proposals.jsonl").exists()
    jobs = (tmp_path / ".soul" / "state" / "queue" / "jobs.jsonl").read_text(encoding="utf-8")
    assert '"source": "deepseek-harness"' in jobs
    assert '"search_limit": 2' in jobs
    runs = (tmp_path / ".soul" / "state" / "integration_runs.jsonl").read_text(encoding="utf-8")
    assert '"operation": "enqueue_evidence"' in runs
    assert '"host": "deepseek-harness"' in runs


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
    proposal = payload["patch_proposal"]
    assert proposal["status"] == "proposed"
    assert proposal["evidence"]["memory_owner"] == "reme"
    assert proposal["evidence"]["reme"]["workspace_dir"].replace("\\", "/").endswith(".soul/reme")
    assert proposal["evidence"]["reme"]["write_mode"] == "auto_memory"
    assert proposal["evidence"]["content"] == "ReMe evidence refs attached; ordinary memory body remains in ReMe."
    assert "先验证 MLD。" not in proposal["evidence"]["content"]
    assert payload["trace_path"] == ".soul/traces/reme_state_trace.md"
    trace = (tmp_path / ".soul" / "traces" / "reme_state_trace.md").read_text(encoding="utf-8")
    assert "reme://daily/2026-08-16/dsh_demo.md:1-12#chunk-1" in trace
    assert proposal["id"] in trace
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
    assert payload["patch_proposal"]["evidence"]["reme"]["workspace_dir"].replace("\\", "/").endswith(".soul/reme")


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


def test_soul_api_reme_transition_falls_back_when_auto_memory_needs_credentials(tmp_path, monkeypatch):
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

    payload = SoulApi(tmp_path).propose_reme_transition(
        evidence={"task": "Fallback from auto_memory", "summary": "Use daily write"},
        reme={"write_mode": "auto_memory"},
    )

    assert [call[0] for call in calls] == ["auto_memory", "daily_write", "search"]
    assert payload["reme_write_mode"] == "fallback_daily_write"
    assert payload["reme_requested_write_mode"] == "auto_memory"
    assert payload["patch_proposal"]["evidence"]["reme"]["requested_write_mode"] == "auto_memory"
    assert {"type": "reme_file", "path": "daily/2026-08-17/fallback.md"} in payload["evidence_refs"]


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
