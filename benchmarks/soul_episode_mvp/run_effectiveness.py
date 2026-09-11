from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from soul.adapters.reme import ReMeJobResult
from soul.api import SoulApi
from soul.services.integrations.episodes import read_episode_events, read_episode_views
from soul.services.integrations.queue import drain_queue, queue_status, replay_queue_state
from soul.services.reme.reme_transition import propose_reme_transition
from soul.services.state_core.state_store import load_state, state_paths
from soul.services.state_core.working_state import load_working_state


class TimeoutError(Exception):
    pass


class StubReMeAdapter:
    def __init__(self, project_dir: Path, workspace_dir: Path | None = None) -> None:
        self.project_dir = project_dir
        self.workspace_dir = workspace_dir or project_dir / ".soul" / "reme"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def daily_write(self, **kwargs: Any) -> ReMeJobResult:
        path = self.workspace_dir / "daily" / "stub.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(kwargs.get("content") or ""), encoding="utf-8")
        return ReMeJobResult(
            job="daily_write",
            command=[],
            returncode=0,
            stdout="",
            stderr="",
            answer="ok",
            metadata={"daily_path": str(path)},
        )

    def auto_memory(self, **kwargs: Any) -> ReMeJobResult:
        path = self.workspace_dir / "auto_memory.json"
        path.write_text(json.dumps(kwargs, ensure_ascii=False, indent=2), encoding="utf-8")
        return ReMeJobResult(
            job="auto_memory",
            command=[],
            returncode=0,
            stdout="",
            stderr="",
            answer="ok",
            metadata={"source_conversation": str(path)},
        )

    def search(self, **kwargs: Any) -> ReMeJobResult:
        return ReMeJobResult(
            job="search",
            command=[],
            returncode=0,
            stdout="",
            stderr="",
            answer="ok",
            metadata={
                "results": [
                    {
                        "path": "daily/stub.md",
                        "id": "stub-chunk",
                        "start_line": 1,
                        "end_line": 8,
                        "score": 0.9,
                    }
                ],
                "counts": {"returned": 1},
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Soul episode reflection MVP effectiveness checks.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Optional temp project directory to use.")
    parser.add_argument("--real-reme", action="store_true", help="Use the real ReMe CLI with fallback_daily_write.")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--keep-project", action="store_true", help="Do not delete the generated temp project.")
    args = parser.parse_args()

    if args.project_dir is None:
        project_dir = Path(tempfile.mkdtemp(prefix="soul-episode-mvp-"))
    else:
        project_dir = args.project_dir.expanduser().resolve()
        project_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = run_effectiveness_check(
            project_dir,
            use_stub_reme=not args.real_reme,
            timeout_seconds=args.timeout_seconds,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["all_passed"] else 1
    finally:
        if args.project_dir is None and not args.keep_project:
            shutil.rmtree(project_dir, ignore_errors=True)


def run_effectiveness_check(project_dir: Path, *, use_stub_reme: bool, timeout_seconds: int) -> dict[str, Any]:
    with timeout_guard(timeout_seconds):
        api = SoulApi(project_dir, register=False)
        cases = effectiveness_cases()
        if use_stub_reme:
            with patch("soul.services.integrations.queue.propose_reme_transition", transition_with_stub_adapter):
                return execute_cases(api, project_dir, cases, mode="stub_reme")
        return execute_cases(api, project_dir, cases, mode="real_reme")


def transition_with_stub_adapter(**kwargs: Any) -> dict[str, Any]:
    return propose_reme_transition(**kwargs, adapter_class=StubReMeAdapter)


def execute_cases(api: SoulApi, project_dir: Path, cases: list[tuple[dict[str, Any], str]], *, mode: str) -> dict[str, Any]:
    queued = [api.enqueue_episode_event(event, promotion=promotion, start_worker=False) for event, promotion in cases]
    duplicate = api.enqueue_episode_event(cases[0][0], promotion="off", start_worker=False)
    drain_result = drain_queue(project_dir, limit=20)
    metrics = effectiveness_metrics(project_dir)
    return {
        "project_dir": str(project_dir),
        "mode": mode,
        "queued_jobs": len(queued),
        "duplicate_job_queued": bool(duplicate.get("job_id")),
        "drain_processed": len(drain_result["processed"]),
        "drain_statuses": [item.get("status") for item in drain_result["processed"]],
        "job_types": [job.get("type") for job in replay_queue_state(project_dir)["jobs"]],
        "metrics": metrics,
        "all_passed": all(metrics.values()),
        "working_state": working_state_summary(project_dir),
        "accepted_matches": accepted_match_statements(project_dir),
        "promotions": [item.get("promotion") for item in drain_result["processed"] if item.get("promotion")],
    }


def effectiveness_cases() -> list[tuple[dict[str, Any], str]]:
    return [
        (
            episode_event(
                "working",
                {
                    "task": {"text": "Capture durable project queue preference", "source": "effectiveness"},
                    "conversation": {
                        "messages": [
                            {"role": "system", "content": "<system-reminder>ignore this</system-reminder>"},
                            {"role": "user", "content": "I prefer episode queues to store lightweight envelopes."},
                        ],
                    },
                    "execution": {"status": "completed"},
                    "summary": "User stated a reusable preference about episode queues.",
                    "working_state": {
                        "route": "working_state",
                        "kind": "preference",
                        "statement": "Effectiveness test: episode queues should store lightweight envelopes.",
                        "reason": "The episode included an explicit reusable project preference.",
                        "scope": "Soul episode reflection effectiveness",
                        "confidence": 0.82,
                    },
                },
            ),
            "off",
        ),
        (
            episode_event(
                "nostate",
                {
                    "task": {"text": "Run one-off temporary action", "source": "effectiveness"},
                    "execution": {"status": "completed"},
                    "summary": "One-off action that should not become state.",
                    "working_state": {"route": "no_state", "reason": "One-off action."},
                },
            ),
            "off",
        ),
        (
            episode_event(
                "promote",
                {
                    "task": {"text": "Promote safe project preference", "source": "effectiveness"},
                    "execution": {"status": "completed"},
                    "summary": "Promotion-on safe preference should enter accepted state.",
                    "working_state": {
                        "route": "working_state",
                        "kind": "preference",
                        "statement": "Effectiveness test: safe non-review episode preferences can be promoted.",
                        "reason": "Promotion was explicitly enabled and the candidate was not review-gated.",
                        "scope": "Soul episode reflection effectiveness",
                        "confidence": 0.86,
                        "review_candidate": False,
                    },
                },
            ),
            "on",
        ),
        (
            episode_event(
                "review",
                {
                    "task": {"text": "Block review-gated promotion", "source": "effectiveness"},
                    "execution": {"status": "completed"},
                    "summary": "Review-gated candidate should not auto-promote.",
                    "working_state": {
                        "route": "working_state",
                        "kind": "constraint",
                        "statement": "Effectiveness test: review candidates stay out of accepted state during auto-promotion.",
                        "reason": "This item is marked as requiring review.",
                        "scope": "Soul episode reflection effectiveness",
                        "confidence": 0.8,
                        "review_candidate": True,
                    },
                },
            ),
            "on",
        ),
    ]


def episode_event(case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": f"effectiveness-{case_id}",
        "event_id": f"effectiveness-event-{case_id}",
        "event_type": "turn_completed",
        "host": "effectiveness-test",
        "project_id": "effectiveness-project",
        "session_id": "effectiveness-session",
        "idempotency_key": f"effectiveness:{case_id}",
        "payload": {
            **payload,
            "reme": {"write_mode": "fallback_daily_write"},
        },
    }


def effectiveness_metrics(project_dir: Path) -> dict[str, bool]:
    working = load_working_state(project_dir)["items"]
    accepted = load_state(project_dir)["current_state"]["state_items"]
    queue = queue_status(project_dir)
    state_root = state_paths(project_dir).state_path.parent
    artifact_files = [path for path in (state_root / "episode_artifacts").rglob("*") if path.is_file()]
    reme_files = [path for path in (project_dir / ".soul" / "reme").rglob("*") if path.is_file()]

    expected_working = {
        "Effectiveness test: episode queues should store lightweight envelopes.",
        "Effectiveness test: safe non-review episode preferences can be promoted.",
        "Effectiveness test: review candidates stay out of accepted state during auto-promotion.",
    }
    expected_no_state = "One-off action that should not become state."
    safe_promotion = "Effectiveness test: safe non-review episode preferences can be promoted."
    unsafe_promotion = "Effectiveness test: review candidates stay out of accepted state during auto-promotion."

    working_statements = {str(item.get("statement") or "") for item in working}
    accepted_statements = {str(item.get("statement") or "") for item in accepted}
    unexpected_working = [
        statement
        for statement in working_statements
        if statement.startswith("Effectiveness test:") and statement not in expected_working
    ]

    return {
        "unique_events": len(read_episode_events(project_dir)) == 4,
        "episode_views": len(read_episode_views(project_dir)) == 4,
        "queue_clean": queue.queued == 0 and queue.blocked == 0 and queue.dead_letter == 0,
        "working_precision": not unexpected_working,
        "working_recall": expected_working.issubset(working_statements),
        "noise_rejection": expected_no_state not in working_statements,
        "duplicate_suppression": len(read_episode_events(project_dir)) == 4,
        "promotion_safety": unsafe_promotion not in accepted_statements,
        "safe_promotion_recall": safe_promotion in accepted_statements,
        "message_artifact_written": any(path.name == "messages.json" for path in artifact_files),
        "reme_refs_attached": all(item.get("evidence_refs") for item in working),
        "reme_artifact_written": bool(reme_files),
    }


def working_state_summary(project_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "status": item.get("status"),
            "review_candidate": item.get("review_candidate"),
            "statement": item.get("statement"),
        }
        for item in load_working_state(project_dir)["items"]
    ]


def accepted_match_statements(project_dir: Path) -> list[str]:
    return [
        str(item.get("statement") or "")
        for item in load_state(project_dir)["current_state"]["state_items"]
        if str(item.get("statement") or "").startswith("Effectiveness test:")
    ]


class timeout_guard:
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        self.previous_handler: Any = None

    def __enter__(self) -> None:
        if not hasattr(signal, "SIGALRM"):
            return
        self.previous_handler = signal.signal(signal.SIGALRM, self.raise_timeout)
        signal.alarm(self.seconds)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not hasattr(signal, "SIGALRM"):
            return
        signal.alarm(0)
        if self.previous_handler is not None:
            signal.signal(signal.SIGALRM, self.previous_handler)

    @staticmethod
    def raise_timeout(signum: int, frame: object) -> None:
        raise TimeoutError("Soul episode MVP effectiveness check timed out.")


if __name__ == "__main__":
    raise SystemExit(main())
