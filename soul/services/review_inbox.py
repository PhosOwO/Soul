from __future__ import annotations

from pathlib import Path
from typing import Any

from soul.services.daemon import load_review_index, refresh_registered_project, scan_registered_projects
from soul.services.project_resolver import load_project_registry, state_owner_dir_from_record
from soul.services.state_core.review.actions import (
    accept_review_candidate,
    edit_review_candidate,
    expire_review_candidate,
    extend_review_candidate,
    reject_review_candidate,
    snooze_review_candidate,
)
from soul.services.state_core.review.card import build_review_card


class ReviewInboxService:
    def __init__(self, *, preferred_project_dir: Path | None = None) -> None:
        self.preferred_project_dir = preferred_project_dir.expanduser().resolve() if preferred_project_dir else None

    def review_index(self, *, scan: bool = False, limit: int = 5, near_expiry_hours: int = 4) -> dict[str, Any]:
        index = (
            scan_registered_projects(limit=limit, near_expiry_hours=near_expiry_hours)
            if scan
            else load_review_index()
        )
        return self.decorate_review_index(index)

    def decorate_review_index(self, index: dict[str, Any]) -> dict[str, Any]:
        projects = [item for item in index.get("projects", []) if isinstance(item, dict)]
        decorated = [{**project, "in_default_inbox": self.in_default_inbox(project)} for project in projects]
        preferred = self.preferred_project_id(decorated)
        return {
            **index,
            "project_count": len(decorated),
            "preferred_project_id": preferred,
            "projects": decorated,
        }

    def preferred_project_id(self, projects: list[dict[str, Any]]) -> str | None:
        if self.preferred_project_dir is not None:
            preferred_path = str(self.preferred_project_dir)
            for project in projects:
                if project.get("available") is False or project.get("status") == "unavailable":
                    continue
                if str(Path(str(project.get("project_dir") or ".")).expanduser().resolve()) == preferred_path:
                    return str(project.get("project_id") or "")
        for project in projects:
            if project.get("in_default_inbox") and project.get("project_id"):
                return str(project["project_id"])
        return None

    @staticmethod
    def in_default_inbox(project: dict[str, Any]) -> bool:
        if project.get("available") is False or project.get("status") in {"unavailable", "archived"}:
            return False
        review = project.get("review") if isinstance(project.get("review"), dict) else {}
        queue = project.get("queue") if isinstance(project.get("queue"), dict) else {}
        return int(review.get("total") or 0) > 0 or int(queue.get("backlog") or 0) > 0

    def review_card(self, project_id: str, *, limit: int = 5, near_expiry_hours: int = 4) -> dict[str, Any]:
        project_dir, record = self.project_review_target(project_id)
        source_project_dir = Path(str(record.get("project_dir") or project_dir)).expanduser().resolve()
        card = build_review_card(
            project_dir,
            limit=limit,
            near_expiry_hours=near_expiry_hours,
            project_name=str(record.get("project_name") or source_project_dir.name),
            source_project_dir=source_project_dir,
        )
        card["project_id"] = record.get("project_id")
        card["state_root"] = record.get("state_root")
        card["storage"] = record.get("storage")
        return card

    def project_review_target(self, project_id: str) -> tuple[Path, dict[str, Any]]:
        registry = load_project_registry()
        for record in registry.get("projects", []):
            if isinstance(record, dict) and record.get("project_id") == project_id:
                return state_owner_dir_from_record(record), record
        raise ValueError(f"Unknown project_id: {project_id}")

    def accept(self, project_id: str, candidate_id: str, *, confirmed_by: str) -> dict[str, Any]:
        project_dir, _record = self.project_review_target(project_id)
        result = accept_review_candidate(project_dir, candidate_id, confirmed_by=confirmed_by)
        refresh_registered_project(project_id)
        return result

    def reject(self, project_id: str, candidate_id: str, *, reason: str = "", rejected_by: str) -> dict[str, Any]:
        project_dir, _record = self.project_review_target(project_id)
        result = reject_review_candidate(project_dir, candidate_id, reason=reason, rejected_by=rejected_by)
        refresh_registered_project(project_id)
        return result

    def edit(self, project_id: str, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        project_dir, _record = self.project_review_target(project_id)
        result = edit_review_candidate(project_dir, candidate_id, payload)
        refresh_registered_project(project_id)
        return result

    def snooze(self, project_id: str, candidate_id: str, *, hours: int = 24) -> dict[str, Any]:
        project_dir, _record = self.project_review_target(project_id)
        result = snooze_review_candidate(project_dir, candidate_id, hours=hours)
        refresh_registered_project(project_id)
        return result

    def expire(self, project_id: str, candidate_id: str, *, reason: str = "") -> dict[str, Any]:
        project_dir, _record = self.project_review_target(project_id)
        result = expire_review_candidate(project_dir, candidate_id, reason=reason)
        refresh_registered_project(project_id)
        return result

    def extend(self, project_id: str, candidate_id: str, *, hours: int = 24) -> dict[str, Any]:
        project_dir, _record = self.project_review_target(project_id)
        result = extend_review_candidate(project_dir, candidate_id, hours=hours)
        refresh_registered_project(project_id)
        return result
