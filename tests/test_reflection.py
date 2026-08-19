from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from soul.services.integrations.episodes import append_episode, next_episode_id, read_system_events
from soul.services.integrations.reflection import extract_cognitive_diffs, load_reflection_policy, load_reflection_rules, reflect_episode
from soul.services.state import load_patch_proposals, load_state


def cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_cli(tmp_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "soul.cli", *args],
        cwd=tmp_path,
        check=check,
        capture_output=True,
        text=True,
        env=cli_env(),
    )


def write_session(path: Path, text: str) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"session_id": "s1"}}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )


def insert_episode(project_dir: Path, messages: list[dict[str, str]], summary: str = "episode") -> int:
    episode_id = next_episode_id(project_dir)
    append_episode(
        project_dir,
        {
            "id": episode_id,
            "source": "codex",
            "source_path": None,
            "summary": summary,
            "messages": messages,
            "metadata": {},
        },
    )
    return episode_id


def test_load_reflection_rules_are_state_patch_signals() -> None:
    rules = load_reflection_rules()

    assert any(rule.patch_type == "Idea" for rule in rules)
    assert not any(rule.patch_type in {"Goal", "Task"} for rule in rules)


def test_reflection_policy_does_not_encode_task_language() -> None:
    policy = load_reflection_policy()

    assert not hasattr(policy, "task_markers")


def test_task_instruction_defaults_to_no_change() -> None:
    messages = [
        {
            "role": "user",
            "text": "我需要基于 event_state_head_geo_0631a_time_split 完成 MHW Category I-IV 统计。",
        },
    ]

    diffs = extract_cognitive_diffs(messages)

    assert diffs == []


def test_extract_diffs_keeps_lightweight_state_patch_proposals() -> None:
    messages = [
        {
            "role": "user",
            "text": "我们是不是应该考虑加入 MLD 作为输入变量？现在可能缺少次表层信息。",
            "timestamp": "2026-08-01T12:07:29Z",
        },
    ]

    diffs = extract_cognitive_diffs(messages)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "STATE_PATCH_PROPOSAL"
    assert diffs[0].content is not None
    assert "MLD" in diffs[0].content
    assert diffs[0].evidence is not None
    assert diffs[0].evidence["criteria"] == "positive_state_patch_signal"
    assert diffs[0].requires_user_confirmation is True


def test_reflect_episode_records_no_change_as_system_event(tmp_path: Path) -> None:
    messages = [
        {
            "role": "user",
            "text": "我需要完成 category confusion matrix 和 per-category recall。",
            "timestamp": "2026-08-01T12:07:29Z",
        }
    ]

    load_state(tmp_path, project_name="Test Project")
    episode_id = insert_episode(tmp_path, messages, "task")
    proposal_ids = reflect_episode(episode_id, project_dir=tmp_path)
    events = read_system_events(tmp_path)

    assert proposal_ids == []
    assert any(event.get("type") == "episode_reflected" and "NO_CHANGE" in event.get("data", {}).get("diff_types", []) for event in events)
    assert not (tmp_path / ".soul" / "state" / "soul.db").exists()


def test_reflect_episode_writes_patch_proposal(tmp_path: Path, monkeypatch) -> None:
    messages = [
        {
            "role": "user",
            "text": "我们决定 Soul 不再保留 Entity/Candidate 兼容路径，核心只走 State Patch。",
            "timestamp": "2026-08-01T12:08:29Z",
        }
    ]
    monkeypatch.chdir(tmp_path)

    load_state(tmp_path, project_name="Test Project")
    episode_id = insert_episode(tmp_path, messages, "decision")
    proposal_ids = reflect_episode(episode_id, project_dir=tmp_path)

    proposals = load_patch_proposals(tmp_path)

    assert len(proposal_ids) == 1
    assert proposals[-1]["id"] == proposal_ids[0]
    assert proposals[-1]["status"] == "proposed"
    assert not (tmp_path / ".soul" / "state" / "soul.db").exists()


def test_cli_reflect_outputs_patch_proposal_not_candidate(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    write_session(session_path, "我们决定 Soul 的最小闭环只走 Current State 和 State Patch。")

    run_cli(tmp_path, "init", "--project-name", "Test Project")
    run_cli(tmp_path, "import", "codex", str(session_path))
    reflected = run_cli(tmp_path, "reflect", "episode", "1")
    status = run_cli(tmp_path, "status")
    entity_command = run_cli(tmp_path, "entity", "list", check=False)

    assert "Patch proposals:" in reflected.stdout
    assert "Created candidates" not in reflected.stdout
    assert "Soul Current State" in status.stdout
    assert entity_command.returncode != 0
