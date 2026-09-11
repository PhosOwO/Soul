from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from soul import __version__
from soul.adapters.reme import ReMeCliAdapter
from soul.services.shared.constants import (
    HOST_HTTP_API,
    HOST_SOUL_HTTP_API,
    PATCH_STATUS_APPLIED,
    MEMORY_MODE_SOUL_REME,
    OP_CONSOLIDATE_MEMORY,
    OP_ENQUEUE_EVIDENCE,
    OP_GET_PROACTIVE_TOPICS,
    OP_GET_STATE,
    OP_READ_EVIDENCE,
    OP_TRACE_EVIDENCE,
    STATUS_SUCCESS,
)
from soul.services.reme.reme_refs import compact_transition_summary, resolve_reme_workspace
from soul.services.reme.reme_transition import propose_reme_transition as propose_reme_transition_service
from soul.services.integrations.integration_runs import append_integration_run
from soul.services.integrations.queue import enqueue_episode_event, enqueue_turn_evidence, stable_turn_id
from soul.services.integrations.sessions import resolve_session_id
from soul.services.scan_core import refresh_registered_project_for_state_owner
from soul.services.project_resolver import register_project, resolve_project_dir
from soul.services.review_inbox import ReviewInboxService
from soul.services.state_core.proposals import apply_patch_proposal, append_patch_status, propose_patch
from soul.services.state_core.review.actions import (
    accept_review_candidate,
    edit_review_candidate,
    expire_review_candidate,
    extend_review_candidate,
    reject_review_candidate,
    snooze_review_candidate,
)
from soul.services.state_core.review.card import build_review_card
from soul.front.review_ui import render_review_page
from soul.services.state_core.state_store import (
    append_patch_proposal,
    find_patch_proposal,
    load_state,
    load_state_markdown,
    save_state,
)
from soul.services.state_core.state_render import format_accepted_state_injection
from soul.services.shared.state_types import PatchProposal


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class SoulApi:
    def __init__(self, project_dir: Path | None = None, *, register: bool = True) -> None:
        self.project_dir = resolve_project_dir(project_dir)
        self.review_inbox = ReviewInboxService(preferred_project_dir=self.project_dir)
        if register:
            register_project(self.project_dir)

    def get_state(self, task: str = "", scope: str = "project", limit: int = 10, source: str = HOST_HTTP_API) -> dict[str, Any]:
        state = load_state(self.project_dir, project_name=self.project_dir.name)
        injection_context = format_accepted_state_injection(state, limit=limit, task=task)
        payload = {
            "task": task,
            "state": state,
            "context": load_state_markdown(self.project_dir, limit=limit, task=task),
            "state_artifact": ".soul/state/STATE.md",
        }
        payload["scope"] = scope
        payload["injection"] = build_agent_injection(injection_context)
        self.record_integration_run(
            {
                "host": source,
                "operation": OP_GET_STATE,
                "status": STATUS_SUCCESS,
                "injected": bool(payload.get("injection")),
                "injected_state_chars": len(injection_context),
                "injected_state_item_count": injection_context.count("\n- ["),
                "task": compact_transition_summary(task, 160),
                "state_version": (payload.get("state") or {}).get("version") if isinstance(payload.get("state"), dict) else None,
            }
        )
        return payload

    def propose_reme_transition(
        self,
        evidence: dict[str, Any] | None = None,
        episode: dict[str, Any] | None = None,
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return propose_reme_transition_service(
            project_dir=self.project_dir,
            evidence=evidence,
            episode=episode,
            reme=reme,
            record_integration_run=self.record_integration_run,
            adapter_class=ReMeCliAdapter,
        )

    def enqueue_evidence(
        self,
        evidence: dict[str, Any] | None = None,
        episode: dict[str, Any] | None = None,
        reme: dict[str, Any] | None = None,
        *,
        start_worker: bool = True,
    ) -> dict[str, Any]:
        evidence_payload = dict(evidence or {})
        episode_payload = episode if isinstance(episode, dict) else {}
        task = str(evidence_payload.get("task") or episode_payload.get("task") or "")
        outcome = str(
            evidence_payload.get("outcome")
            or evidence_payload.get("summary")
            or episode_payload.get("outcome")
            or ""
        )
        source = str(evidence_payload.get("source") or HOST_HTTP_API)
        session_id = resolve_session_id(host=source, project_dir=self.project_dir, payloads=[evidence_payload, episode_payload])
        turn_payload = {
            **evidence_payload,
            "source": source,
            "task": task,
            "outcome": outcome,
            "summary": str(evidence_payload.get("summary") or outcome),
            "session_id": session_id,
        }
        if isinstance(evidence_payload.get("messages"), list):
            messages = evidence_payload["messages"]
        elif isinstance(episode_payload.get("messages"), list):
            messages = episode_payload["messages"]
        else:
            messages = normalized_messages(task, outcome)
        turn_payload["messages"] = messages
        if isinstance(evidence_payload.get("events"), list):
            turn_payload["events"] = evidence_payload["events"]
        elif isinstance(episode_payload.get("events"), list):
            turn_payload["events"] = episode_payload["events"]
        else:
            turn_payload["events"] = []
        filtered_reme = enqueue_reme_options(reme)
        if filtered_reme:
            turn_payload["reme"] = filtered_reme
        turn_id = str(evidence_payload.get("turn_id") or episode_payload.get("turn_id") or stable_turn_id(turn_payload))
        job = enqueue_turn_evidence(
            self.project_dir,
            source=source,
            session_id=session_id,
            turn_id=turn_id,
            payload=turn_payload,
        )
        background_drain_started = False
        if start_worker:
            from soul.hooks.runtime import start_queue_drain

            background_drain_started = start_queue_drain(self.project_dir)
        self.record_integration_run(
            {
                "host": source,
                "operation": OP_ENQUEUE_EVIDENCE,
                "status": STATUS_SUCCESS,
                "memory_mode": MEMORY_MODE_SOUL_REME,
                "task": compact_transition_summary(task, 160),
                "job_id": job.get("job_id"),
                "session_id": session_id,
                "turn_id": turn_id,
                "background_drain_started": background_drain_started,
            }
        )
        self.refresh_current_review_project()
        return {
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "queued": True,
            "job_id": job.get("job_id"),
            "session_id": session_id,
            "turn_id": turn_id,
            "background_drain_started": background_drain_started,
            "queue_path": ".soul/state/queue/jobs.jsonl",
        }

    def enqueue_episode_event(
        self,
        event: dict[str, Any],
        *,
        promotion: str = "off",
        start_worker: bool = True,
    ) -> dict[str, Any]:
        job = enqueue_episode_event(self.project_dir, event=event, promotion=promotion)
        background_drain_started = False
        if start_worker:
            from soul.hooks.runtime import start_queue_drain

            background_drain_started = start_queue_drain(self.project_dir)
        self.record_integration_run(
            {
                "host": str(event.get("host") or HOST_HTTP_API),
                "operation": "enqueue_episode_event",
                "status": STATUS_SUCCESS,
                "memory_mode": MEMORY_MODE_SOUL_REME,
                "episode_id": event.get("episode_id"),
                "event_type": event.get("event_type"),
                "job_id": job.get("job_id"),
                "background_drain_started": background_drain_started,
            }
        )
        self.refresh_current_review_project()
        return {
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "queued": True,
            "job_id": job.get("job_id"),
            "episode_id": (job.get("event") or {}).get("episode_id") if isinstance(job.get("event"), dict) else event.get("episode_id"),
            "event_type": (job.get("event") or {}).get("event_type") if isinstance(job.get("event"), dict) else event.get("event_type"),
            "background_drain_started": background_drain_started,
            "queue_path": ".soul/state/queue/jobs.jsonl",
        }

    def record_integration_run(self, record: dict[str, Any]) -> None:
        try:
            append_integration_run(self.project_dir, record)
        except OSError:
            return

    def read_evidence(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, (reme or {}).get("workspace_dir")),
        )
        result = adapter.read(path=path, start_line=start_line, end_line=end_line)
        return {
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "operation": OP_READ_EVIDENCE,
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def trace_evidence(
        self,
        path: str,
        depth: int = 1,
        direction: str = "both",
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, (reme or {}).get("workspace_dir")),
        )
        result = adapter.traverse(path=path, depth=depth, direction=direction)
        return {
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "operation": OP_TRACE_EVIDENCE,
            "path": path,
            "depth": depth,
            "direction": direction,
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def consolidate_memory(
        self,
        date: str = "",
        hint: str = "",
        scan_days: int | None = None,
        max_units: int | None = None,
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, (reme or {}).get("workspace_dir")),
        )
        result = adapter.auto_dream(date=date, hint=hint, scan_days=scan_days, max_units=max_units)
        return {
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "operation": OP_CONSOLIDATE_MEMORY,
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def get_proactive_topics(
        self,
        date: str = "",
        include_content: bool = False,
        reme: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = ReMeCliAdapter(
            self.project_dir,
            workspace_dir=resolve_reme_workspace(self.project_dir, (reme or {}).get("workspace_dir")),
        )
        result = adapter.proactive(date=date, include_content=include_content)
        return {
            "memory_mode": MEMORY_MODE_SOUL_REME,
            "operation": OP_GET_PROACTIVE_TOPICS,
            "answer": result.answer,
            "metadata": result.metadata,
        }

    def propose_patch(self, evidence: dict[str, Any]) -> PatchProposal:
        proposal = propose_patch(load_state(self.project_dir), evidence, source=evidence.get("source", HOST_SOUL_HTTP_API))
        append_patch_proposal(proposal, self.project_dir)
        return proposal

    def apply_patch(self, proposal_id: str, confirmed_by: str = HOST_SOUL_HTTP_API) -> dict[str, Any]:
        proposal = find_patch_proposal(proposal_id, self.project_dir)
        state = load_state(self.project_dir)
        if proposal.get("base_version") != state.get("version"):
            raise ValueError(
                f"Patch base version {proposal.get('base_version')} does not match current state version {state.get('version')}."
            )
        next_state = apply_patch_proposal(state, proposal, confirmed_by=confirmed_by)
        save_state(next_state, self.project_dir)
        append_patch_status(proposal, PATCH_STATUS_APPLIED, self.project_dir, updated_by=confirmed_by)
        return {"state": next_state, "applied_patch": proposal}

    def review_index(self, *, scan: bool = False, limit: int = 5, near_expiry_hours: int = 4) -> dict[str, Any]:
        return self.review_inbox.review_index(scan=scan, limit=limit, near_expiry_hours=near_expiry_hours)

    def project_review_target(self, project_id: str) -> tuple[Path, dict[str, Any]]:
        return self.review_inbox.project_review_target(project_id)

    def review_card(
        self,
        limit: int = 5,
        near_expiry_hours: int = 4,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if not project_id:
            return build_review_card(self.project_dir, limit=limit, near_expiry_hours=near_expiry_hours)
        return self.review_inbox.review_card(project_id, limit=limit, near_expiry_hours=near_expiry_hours)

    def refresh_review_index(self, *, limit: int = 5, near_expiry_hours: int = 4) -> dict[str, Any]:
        return self.review_inbox.review_index(scan=True, limit=limit, near_expiry_hours=near_expiry_hours)

    def refresh_current_review_project(self) -> dict[str, Any] | None:
        return refresh_registered_project_for_state_owner(self.project_dir)

    def accept_review_candidate(self, candidate_id: str, confirmed_by: str = HOST_SOUL_HTTP_API) -> dict[str, Any]:
        result = accept_review_candidate(self.project_dir, candidate_id, confirmed_by=confirmed_by)
        self.refresh_current_review_project()
        return result

    def accept_project_review_candidate(
        self,
        project_id: str,
        candidate_id: str,
        confirmed_by: str = HOST_SOUL_HTTP_API,
    ) -> dict[str, Any]:
        return self.review_inbox.accept(project_id, candidate_id, confirmed_by=confirmed_by)

    def reject_review_candidate(
        self,
        candidate_id: str,
        *,
        reason: str = "",
        rejected_by: str = HOST_SOUL_HTTP_API,
    ) -> dict[str, Any]:
        result = reject_review_candidate(self.project_dir, candidate_id, reason=reason, rejected_by=rejected_by)
        self.refresh_current_review_project()
        return result

    def reject_project_review_candidate(
        self,
        project_id: str,
        candidate_id: str,
        *,
        reason: str = "",
        rejected_by: str = HOST_SOUL_HTTP_API,
    ) -> dict[str, Any]:
        return self.review_inbox.reject(project_id, candidate_id, reason=reason, rejected_by=rejected_by)

    def edit_review_candidate(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = edit_review_candidate(self.project_dir, candidate_id, payload)
        self.refresh_current_review_project()
        return result

    def edit_project_review_candidate(self, project_id: str, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.review_inbox.edit(project_id, candidate_id, payload)

    def snooze_review_candidate(self, candidate_id: str, *, hours: int = 24) -> dict[str, Any]:
        result = snooze_review_candidate(self.project_dir, candidate_id, hours=hours)
        self.refresh_current_review_project()
        return result

    def snooze_project_review_candidate(self, project_id: str, candidate_id: str, *, hours: int = 24) -> dict[str, Any]:
        return self.review_inbox.snooze(project_id, candidate_id, hours=hours)

    def expire_review_candidate(self, candidate_id: str, *, reason: str = "") -> dict[str, Any]:
        result = expire_review_candidate(self.project_dir, candidate_id, reason=reason)
        self.refresh_current_review_project()
        return result

    def expire_project_review_candidate(self, project_id: str, candidate_id: str, *, reason: str = "") -> dict[str, Any]:
        return self.review_inbox.expire(project_id, candidate_id, reason=reason)

    def extend_review_candidate(self, candidate_id: str, *, hours: int = 24) -> dict[str, Any]:
        result = extend_review_candidate(self.project_dir, candidate_id, hours=hours)
        self.refresh_current_review_project()
        return result

    def extend_project_review_candidate(self, project_id: str, candidate_id: str, *, hours: int = 24) -> dict[str, Any]:
        return self.review_inbox.extend(project_id, candidate_id, hours=hours)

def build_agent_injection(context: str) -> str:
    if not context.strip():
        return ""
    return (
        "[Soul Current State]\n"
        "Use Accepted State as confirmed project cognition. "
        "Use Working State only as unconfirmed, short-lived operating context. "
        "Do not treat Soul as a planner or executor; only use the state to improve the next answer.\n\n"
        f"{context}\n"
        "[/Soul Current State]"
    )


def normalized_messages(task: str, outcome: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if task:
        messages.append({"role": "user", "content": task})
    if outcome:
        messages.append({"role": "assistant", "content": outcome})
    return messages


def enqueue_reme_options(reme: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reme, dict):
        return {}
    allowed = {"search_limit", "date", "memory_hint"}
    return {key: value for key, value in reme.items() if key in allowed}


def make_handler(api: SoulApi) -> type[BaseHTTPRequestHandler]:
    class SoulApiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self.write_json({"ok": True, "service": HOST_SOUL_HTTP_API, "review_service": True})
                return
            if parsed.path == "/review":
                self.write_html(render_review_page())
                return
            if parsed.path == "/review-card":
                query = parse_qs(parsed.query)
                limit = int_value(first_query_value(query, "limit", "5"), default=5)
                near_expiry_hours = int_value(first_query_value(query, "near_expiry_hours", "4"), default=4)
                project_id = first_query_value(query, "project_id", "")
                if not project_id:
                    index = api.review_index(scan=False, limit=limit, near_expiry_hours=near_expiry_hours)
                    project_id = str(index.get("preferred_project_id") or "")
                if not project_id:
                    self.write_json({"error": "project_id_required"}, status=400)
                    return
                try:
                    self.write_json(
                        api.review_card(
                            limit=limit,
                            near_expiry_hours=near_expiry_hours,
                            project_id=project_id,
                        )
                    )
                except ValueError as exc:
                    self.write_json({"error": str(exc)}, status=400)
                return
            if parsed.path == "/review-index":
                query = parse_qs(parsed.query)
                limit = int_value(first_query_value(query, "limit", "5"), default=5)
                near_expiry_hours = int_value(first_query_value(query, "near_expiry_hours", "4"), default=4)
                scan = first_query_value(query, "scan", "").lower() in {"1", "true", "yes"}
                self.write_json(api.review_index(scan=scan, limit=limit, near_expiry_hours=near_expiry_hours))
                return
            if parsed.path == "/state":
                query = parse_qs(parsed.query)
                task = first_query_value(query, "task", "")
                scope = first_query_value(query, "scope", "project")
                limit = int_value(first_query_value(query, "limit", "10"), default=10)
                source = first_query_value(query, "source", HOST_HTTP_API)
                self.write_json(api.get_state(task=task, scope=scope, limit=limit, source=source))
                return
            self.write_json({"error": "not_found"}, status=404)

        def do_POST(self) -> None:
            try:
                payload = self.read_json()
                if self.path == "/evidence/enqueue":
                    self.write_json(
                        api.enqueue_evidence(
                            evidence=payload.get("evidence"),
                            episode=payload.get("episode"),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/episodes/events":
                    event = payload.get("event")
                    if not isinstance(event, dict):
                        self.write_json({"error": "event_required"}, status=400)
                        return
                    self.write_json(
                        api.enqueue_episode_event(
                            event=event,
                            promotion=str(payload.get("promotion") or "off"),
                        )
                    )
                    return
                if self.path == "/reme/transition/propose":
                    self.write_json(
                        api.propose_reme_transition(
                            evidence=payload.get("evidence"),
                            episode=payload.get("episode"),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/reme/evidence/read":
                    self.write_json(
                        api.read_evidence(
                            path=str(payload["path"]),
                            start_line=optional_int(payload.get("start_line")),
                            end_line=optional_int(payload.get("end_line")),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/reme/evidence/trace":
                    self.write_json(
                        api.trace_evidence(
                            path=str(payload["path"]),
                            depth=int_value(payload.get("depth"), default=1),
                            direction=str(payload.get("direction") or "both"),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/reme/memory/consolidate":
                    self.write_json(
                        api.consolidate_memory(
                            date=str(payload.get("date") or ""),
                            hint=str(payload.get("hint") or ""),
                            scan_days=optional_int(payload.get("scan_days")),
                            max_units=optional_int(payload.get("max_units")),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/reme/proactive":
                    self.write_json(
                        api.get_proactive_topics(
                            date=str(payload.get("date") or ""),
                            include_content=bool(payload.get("include_content", False)),
                            reme=payload.get("reme"),
                        )
                    )
                    return
                if self.path == "/patch/propose":
                    self.write_json(dict(api.propose_patch(payload.get("evidence", payload))))
                    return
                if self.path == "/patch/apply":
                    proposal_id = str(payload["proposal_id"])
                    confirmed_by = str(payload.get("confirmed_by", HOST_SOUL_HTTP_API))
                    self.write_json(api.apply_patch(proposal_id, confirmed_by=confirmed_by))
                    return
                if self.path == "/review/accept":
                    project_id = optional_project_id(payload)
                    if not project_id:
                        self.write_json({"error": "project_id_required"}, status=400)
                        return
                    self.write_json(
                        api.accept_project_review_candidate(
                            project_id,
                            str(payload["candidate_id"]),
                            confirmed_by=str(payload.get("confirmed_by", HOST_SOUL_HTTP_API)),
                        )
                    )
                    return
                if self.path == "/review/reject":
                    project_id = optional_project_id(payload)
                    if not project_id:
                        self.write_json({"error": "project_id_required"}, status=400)
                        return
                    self.write_json(
                        api.reject_project_review_candidate(
                            project_id,
                            str(payload["candidate_id"]),
                            reason=str(payload.get("reason") or ""),
                            rejected_by=str(payload.get("rejected_by", HOST_SOUL_HTTP_API)),
                        )
                    )
                    return
                if self.path == "/review/edit":
                    project_id = optional_project_id(payload)
                    if not project_id:
                        self.write_json({"error": "project_id_required"}, status=400)
                        return
                    self.write_json(api.edit_project_review_candidate(project_id, str(payload["candidate_id"]), payload))
                    return
                if self.path == "/review/snooze":
                    project_id = optional_project_id(payload)
                    if not project_id:
                        self.write_json({"error": "project_id_required"}, status=400)
                        return
                    self.write_json(
                        api.snooze_project_review_candidate(
                            project_id,
                            str(payload["candidate_id"]),
                            hours=int_value(payload.get("hours"), default=24),
                        )
                    )
                    return
                if self.path == "/review/expire":
                    project_id = optional_project_id(payload)
                    if not project_id:
                        self.write_json({"error": "project_id_required"}, status=400)
                        return
                    self.write_json(
                        api.expire_project_review_candidate(
                            project_id,
                            str(payload["candidate_id"]),
                            reason=str(payload.get("reason") or ""),
                        )
                    )
                    return
                if self.path == "/review/extend":
                    project_id = optional_project_id(payload)
                    if not project_id:
                        self.write_json({"error": "project_id_required"}, status=400)
                        return
                    self.write_json(
                        api.extend_project_review_candidate(
                            project_id,
                            str(payload["candidate_id"]),
                            hours=int_value(payload.get("hours"), default=24),
                        )
                    )
                    return
                if self.path == "/review-index/scan":
                    self.write_json(api.refresh_review_index())
                    return
                if self.path == "/shutdown":
                    self.write_json({"ok": True, "service": HOST_SOUL_HTTP_API, "shutdown": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self.write_json({"error": "not_found"}, status=404)
            except Exception as exc:
                self.write_json({"error": str(exc)}, status=400)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def write_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def write_html(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SoulApiHandler


def first_query_value(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default


def int_value(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def optional_project_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("project_id")
    if value in (None, ""):
        return None
    return str(value)


def serve(project_dir: Path, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *, register: bool = True) -> None:
    api = SoulApi(project_dir.resolve(), register=register)
    server = ThreadingHTTPServer((host, port), make_handler(api))
    print(f"Soul HTTP API listening on http://{host}:{port} for {project_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSoul HTTP API stopped by keyboard interrupt.")
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the minimal local Soul HTTP API.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--project-dir", default=".", help="Soul project directory containing .soul/")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serve(Path(args.project_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
