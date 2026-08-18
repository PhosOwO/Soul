from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from soul.services.constants import PATCH_STATUS_ACCEPTED, SOUL_DIR_NAME, STATE_DIR_NAME, STATE_KIND_ACCEPTED_BELIEF, STATE_KIND_ACTIVE_CONSTRAINT
from soul.services.state_types import PatchProposal, StateDoc


DEFAULT_STATE_NAME = "state.json"
DEFAULT_STATE_MARKDOWN_NAME = "STATE.md"
DEFAULT_PATCH_LOG_NAME = "patch_proposals.jsonl"


@dataclass(frozen=True, slots=True)
class StatePaths:
    state_path: Path
    state_markdown_path: Path
    patch_log_path: Path


def brain_dir(project_dir: Path | None = None) -> Path:
    return (project_dir or Path.cwd()) / SOUL_DIR_NAME / STATE_DIR_NAME


def state_paths(project_dir: Path | None = None) -> StatePaths:
    root = brain_dir(project_dir)
    return StatePaths(root / DEFAULT_STATE_NAME, root / DEFAULT_STATE_MARKDOWN_NAME, root / DEFAULT_PATCH_LOG_NAME)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def int_value(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return int(value)


def initial_state(project_name: str = "Soul Project") -> StateDoc:
    now = utc_now()
    return {
        "schema_version": 1,
        "project": project_name,
        "version": 1,
        "updated_at": now,
        "current_state": {
            "beliefs": [
                {
                    "id": "state-centric-loop",
                    "statement": (
                        "Soul's core loop is Current State -> Evidence -> Cognitive Diff "
                        "-> State Patch -> Confirm -> New State."
                    ),
                    "status": PATCH_STATUS_ACCEPTED,
                    "confidence": 0.9,
                    "evidence_count": 1,
                    "latest_evidence": "Initial Soul design correction.",
                    "updated_at": now,
                }
            ],
            "constraints": [
                "Episodes and system logs are evidence, not accepted cognition.",
                "State patches require explicit confirmation before changing accepted state.",
                "Entity and Candidate promotion are not part of the active Soul loop.",
            ],
            "open_questions": [],
            "state_items": [
                {
                    "id": "state-centric-loop",
                    "kind": STATE_KIND_ACCEPTED_BELIEF,
                    "statement": (
                        "Soul's core loop is Current State -> Evidence -> Cognitive Diff "
                        "-> State Patch -> Confirm -> New State."
                    ),
                    "status": PATCH_STATUS_ACCEPTED,
                    "priority": "high",
                    "confidence": 0.9,
                    "evidence_count": 1,
                    "latest_evidence": "Initial Soul design correction.",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "patch-confirm-gate",
                    "kind": STATE_KIND_ACTIVE_CONSTRAINT,
                    "statement": "State patches require explicit confirmation before changing accepted state.",
                    "status": PATCH_STATUS_ACCEPTED,
                    "priority": "high",
                    "confidence": 0.9,
                    "evidence_count": 1,
                    "latest_evidence": "Initial Soul design correction.",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        },
        "history": [],
    }


def load_state(project_dir: Path | None = None, project_name: str = "Soul Project") -> StateDoc:
    paths = state_paths(project_dir)
    if not paths.state_path.exists():
        save_state(initial_state(project_name), project_dir)
    return cast(StateDoc, json.loads(paths.state_path.read_text(encoding="utf-8")))


def save_state(state: StateDoc, project_dir: Path | None = None) -> None:
    from soul.services.state_render import format_state_context

    paths = state_paths(project_dir)
    paths.state_path.parent.mkdir(parents=True, exist_ok=True)
    paths.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths.state_markdown_path.write_text(format_state_context(state) + "\n", encoding="utf-8")


def load_state_markdown(project_dir: Path | None = None, limit: int = 10, task: str = "") -> str:
    from soul.services.state_render import format_state_context

    state = load_state(project_dir)
    context = format_state_context(state, limit=limit, task=task)
    paths = state_paths(project_dir)
    if not paths.state_markdown_path.exists() or not task:
        paths.state_markdown_path.parent.mkdir(parents=True, exist_ok=True)
        paths.state_markdown_path.write_text(format_state_context(state) + "\n", encoding="utf-8")
    return context


def append_patch_proposal(proposal: PatchProposal, project_dir: Path | None = None) -> None:
    paths = state_paths(project_dir)
    paths.patch_log_path.parent.mkdir(parents=True, exist_ok=True)
    with paths.patch_log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(proposal, ensure_ascii=False) + "\n")


def load_patch_proposals(project_dir: Path | None = None) -> list[PatchProposal]:
    paths = state_paths(project_dir)
    if not paths.patch_log_path.exists():
        return []
    return cast(list[PatchProposal], [
        json.loads(line)
        for line in paths.patch_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ])


def find_patch_proposal(proposal_id: str, project_dir: Path | None = None) -> PatchProposal:
    for proposal in reversed(load_patch_proposals(project_dir)):
        if proposal.get("id") == proposal_id:
            return proposal
    raise ValueError(f"Patch proposal not found: {proposal_id}")
