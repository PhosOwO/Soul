from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SESSION_ID_KEYS = (
    "session_id",
    "conversation_id",
    "thread_id",
    "chat_id",
    "run_id",
)

SESSION_NAME_KEYS = (
    "session_name",
    "thread_name",
    "conversation_name",
)


def resolve_session_id(
    *,
    host: str,
    project_dir: Path,
    payloads: list[dict[str, Any]],
    now: datetime | None = None,
) -> str:
    for payload in payloads:
        direct = first_string_value(payload, SESSION_ID_KEYS)
        if direct:
            return direct

    for payload in payloads:
        named = first_string_value(payload, SESSION_NAME_KEYS)
        if named:
            return "session-" + stable_slug({"host": host, "project": str(project_dir.resolve()), "name": named})

    current_time = now or datetime.now(UTC)
    date = current_time.strftime("%Y%m%d")
    project_hash = stable_slug(str(project_dir.resolve()), length=8)
    host_slug = sanitize_session_part(host) or "host"
    return f"soul-{host_slug}-{project_hash}-{date}"


def first_string_value(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def stable_slug(value: object, *, length: int = 12) -> str:
    return hashlib.sha1(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[
        :length
    ]


def sanitize_session_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip()).strip("-")
