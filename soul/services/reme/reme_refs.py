from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from soul.adapters.reme import reme_write_refs
from soul.services.shared.constants import REME_DIR_NAME, SOUL_DIR_NAME, TRACES_DIR_NAME
from soul.services.shared.text import compact_summary


def resolve_reme_workspace(project_dir: Path, raw_workspace: Any) -> Path:
    if not raw_workspace:
        return default_reme_workspace(project_dir)
    path = Path(str(raw_workspace))
    return path if path.is_absolute() else project_dir / path


def default_reme_workspace(project_dir: Path) -> Path:
    return project_dir / SOUL_DIR_NAME / REME_DIR_NAME


def merge_evidence_refs(*ref_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for refs in ref_lists:
        for ref in refs:
            key = (ref.get("type"), ref.get("path"), ref.get("start_line"), ref.get("end_line"), ref.get("chunk_id"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
    return merged


def safe_reme_note_name(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", raw.strip())[:80].strip("_")
    return name or "deepseek_harness_episode"


def safe_reme_session_id(raw: str) -> str:
    session_id = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw.strip())[:120].strip("-")
    return session_id or "soul-agent"


def compact_transition_summary(text: str, limit: int = 240) -> str:
    return compact_summary(
        text,
        limit,
        fallback="Agent episode was written to ReMe; review evidence refs for state changes.",
    )


def append_reme_state_trace(
    project_dir: Path,
    *,
    task: str,
    write_metadata: dict[str, Any],
    search_metadata: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
    proposal: Mapping[str, Any],
) -> Path:
    trace_path = project_dir / SOUL_DIR_NAME / TRACES_DIR_NAME / "reme_state_trace.md"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    counts = search_metadata.get("counts", {})
    lines = [
        f"## {datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
        "",
        f"- task: {compact_transition_summary(task, 160)}",
        f"- reme_write_paths: {', '.join(ref['path'] for ref in reme_write_refs(write_metadata))}",
        f"- search_counts: vector={counts.get('vector', 0)}, keyword={counts.get('keyword', 0)}, returned={counts.get('returned', 0)}, hybrid={counts.get('hybrid', False)}",
        f"- soul_patch_id: {proposal.get('id', '')}",
        f"- soul_patch_status: {proposal.get('status', '')}",
        f"- review_recommendation: {proposal.get('review_recommendation', '')}",
        "- evidence_refs:",
    ]
    if evidence_refs:
        for ref in evidence_refs:
            line_range = ""
            if ref.get("start_line") is not None and ref.get("end_line") is not None:
                line_range = f":{ref['start_line']}-{ref['end_line']}"
            chunk = f"#{ref['chunk_id']}" if ref.get("chunk_id") else ""
            lines.append(f"  - reme://{ref.get('path', '')}{line_range}{chunk}")
    else:
        lines.append("  - none")
    lines.append("")
    with trace_path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))
    return trace_path
