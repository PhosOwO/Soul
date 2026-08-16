from __future__ import annotations

from soul.api import SoulApi
from soul.adapters.reme import ReMeJobResult
from soul.storage.database import connect, default_db_path, init_database


def test_soul_api_get_state_returns_injection(tmp_path):
    with connect(default_db_path(tmp_path)) as conn:
        init_database(conn, project_name="Demo")

    payload = SoulApi(tmp_path).get_state(task="下一步怎么做？")

    assert "Soul Current State" in payload["context"]
    assert "[Soul Current State]" in payload["injection"]
    assert payload["task"] == "下一步怎么做？"


def test_soul_api_propose_transition_records_episode_and_patch(tmp_path):
    with connect(default_db_path(tmp_path)) as conn:
        init_database(conn, project_name="Demo")

    payload = SoulApi(tmp_path).propose_transition(
        evidence={"task": "Review new evidence", "summary": "A new observation needs review before it changes accepted state."},
        episode={"events": [{"type": "assistant_answer", "durable": True}]},
    )

    assert payload["episode_id"] > 0
    assert payload["patch_proposal"]["status"] == "proposed"
    assert payload["patch_proposal"]["operations"]


def test_soul_api_reme_transition_writes_reme_and_proposes_refs_only_patch(tmp_path, monkeypatch):
    with connect(default_db_path(tmp_path)) as conn:
        init_database(conn, project_name="Demo")

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
                metadata={"path": "daily/2026-08-16/dsh_demo.md"},
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
    assert payload["evidence_refs"] == [
        {
            "type": "reme_file_chunk",
            "path": "daily/2026-08-16/dsh_demo.md",
            "chunk_id": "chunk-1",
            "start_line": 1,
            "end_line": 12,
            "score": 2.5,
        }
    ]
    proposal = payload["patch_proposal"]
    assert proposal["status"] == "proposed"
    assert proposal["evidence"]["memory_owner"] == "reme"
    assert proposal["evidence"]["reme"]["workspace_dir"].replace("\\", "/").endswith(".soul/reme")
    assert proposal["evidence"]["content"] == "ReMe evidence refs attached; ordinary memory body remains in ReMe."
    assert "先验证 MLD。" not in proposal["evidence"]["content"]
    assert payload["trace_path"] == ".soul/traces/reme_state_trace.md"
    trace = (tmp_path / ".soul" / "traces" / "reme_state_trace.md").read_text(encoding="utf-8")
    assert "reme://daily/2026-08-16/dsh_demo.md:1-12#chunk-1" in trace
    assert proposal["id"] in trace
