from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from soul.adapters.codex import CodexImportResult, parse_codex_jsonl
from soul.services.integrations.episodes import append_system_event, upsert_episode_by_source_path
from soul.services.integrations.importer import codex_episode_payload
from soul.services.integrations.reflection import reflect_episode


@dataclass(slots=True)
class CodexIngestResult:
    episode_id: int
    imported: bool
    patch_proposal_ids: list[str]
    summary: str


def ingest_codex_session(path: Path, project_dir: Path | None = None, max_patches: int = 3) -> CodexIngestResult:
    result = parse_codex_jsonl(path)
    episode, imported = upsert_codex_episode(result, project_dir=project_dir)
    episode_id = int(episode["id"])
    patch_proposal_ids = reflect_episode(episode_id, project_dir=project_dir, max_patches=max_patches)
    return CodexIngestResult(
        episode_id=episode_id,
        imported=imported,
        patch_proposal_ids=patch_proposal_ids,
        summary=result.summary,
    )


def upsert_codex_episode(result: CodexImportResult, project_dir: Path | None = None) -> tuple[dict, bool]:
    episode, imported = upsert_episode_by_source_path(project_dir, codex_episode_payload(result))
    append_system_event(
        project_dir,
        event_type="episode_imported" if imported else "episode_updated",
        data={"episode_id": episode["id"], "source": "codex"},
        reason=f"{'Imported' if imported else 'Updated'} Codex episode: {result.summary}",
        source="soul codex ingest",
    )
    return episode, imported
