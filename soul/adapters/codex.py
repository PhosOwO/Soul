from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from soul.services.text import compact_text


CONTEXT_PREFIXES = (
    "<recommended_plugins>",
    "<environment_context>",
    "<apps_instructions>",
    "<plugins_instructions>",
    "<skills_instructions>",
    "# AGENTS.md instructions",
)


@dataclass(slots=True)
class CodexImportResult:
    source_path: str
    session_id: str | None
    cwd: str | None
    originator: str | None
    messages: list[dict[str, Any]]
    skipped_messages: int

    @property
    def summary(self) -> str:
        first_user = next((m["text"] for m in self.messages if m["role"] == "user"), None)
        if first_user:
            return compact_text(first_user, max_length=120)
        return f"Codex session {self.session_id or Path(self.source_path).name}"


def parse_codex_jsonl(path: Path) -> CodexImportResult:
    session_id: str | None = None
    cwd: str | None = None
    originator: str | None = None
    messages: list[dict[str, Any]] = []
    skipped_messages = 0

    for obj in read_jsonl(path):
        if obj.get("type") == "session_meta":
            payload = obj.get("payload") or {}
            session_id = payload.get("session_id") or payload.get("id")
            cwd = payload.get("cwd")
            originator = payload.get("originator")
            continue

        if obj.get("type") != "response_item":
            continue

        payload = obj.get("payload") or {}
        role = payload.get("role")
        if role not in {"user", "assistant"}:
            continue

        content = payload.get("content") or []
        if role == "user":
            text = extract_user_message_text(content)
        else:
            text = extract_message_text(content)
        if not text:
            skipped_messages += 1
            continue

        messages.append(
            {
                "role": role,
                "text": text,
                "timestamp": obj.get("timestamp"),
                "phase": payload.get("phase"),
            }
        )

    return CodexImportResult(
        source_path=str(path),
        session_id=session_id,
        cwd=cwd,
        originator=originator,
        messages=messages,
        skipped_messages=skipped_messages,
    )


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}: {path}") from exc


def extract_message_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def extract_user_message_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        stripped = text.strip()
        if not stripped or stripped.startswith(CONTEXT_PREFIXES):
            continue
        parts.append(stripped)
    return "\n\n".join(parts).strip()




