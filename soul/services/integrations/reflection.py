from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePath
from typing import Any

from soul.services.integrations.episodes import append_system_event, find_episode
from soul.services.state import append_patch_proposal, load_state, propose_patch


DEFAULT_RULES_RESOURCE = "reflection_rules.json"
DEFAULT_POLICY_RESOURCE = "reflection_policy.json"
ARTIFACT_PATTERN = re.compile(r"(?<![\w.-])([\w./\\-]+\.(?:py|json|md|toml|yaml|yml|sql|db|csv|ipynb|ts|tsx|js|jsx))(?![\w.-])")


@dataclass(frozen=True, slots=True)
class ReflectionRule:
    patch_type: str
    markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReflectionPolicy:
    min_fragment_length: int
    ignored_prefixes: tuple[str, ...]


@dataclass(slots=True)
class CognitiveDiff:
    diff_type: str
    content: str | None = None
    operation: str | None = None
    requires_user_confirmation: bool = True
    reason: str | None = None
    evidence: dict[str, Any] | None = None
    patch_proposal_id: str | None = None


def reflect_episode(episode_id: int, project_dir: Path | None = None, max_patches: int = 3) -> list[str]:
    episode = find_episode(project_dir, episode_id)
    if episode is None:
        raise ValueError(f"Episode not found: {episode_id}")

    messages = episode_messages(episode)
    artifact_paths = extract_artifact_paths(messages)
    diffs = extract_cognitive_diffs(messages, max_patches=max_patches)
    patch_ids: list[str] = []

    for diff in diffs:
        evidence = dict(diff.evidence or {})
        evidence.update(
            {
                "episode_id": episode_id,
                "source": f"episode:{episode_id}",
                "summary": diff.content or episode.get("summary", ""),
                "content": diff.content or episode_text(messages),
                "artifact_paths": artifact_paths,
            }
        )
        proposal = propose_patch(load_state(project_dir), evidence, source=f"reflection:episode:{episode_id}")
        append_patch_proposal(proposal, project_dir)
        diff.patch_proposal_id = proposal["id"]
        patch_ids.append(proposal["id"])

    append_system_event(
        project_dir,
        event_type="episode_reflected",
        data={
            "episode_id": episode_id,
            "diff_types": [diff.diff_type for diff in diffs] or ["NO_CHANGE"],
            "patch_proposal_ids": patch_ids,
            "artifact_paths": artifact_paths,
        },
        reason=f"Reflected episode {episode_id}: {', '.join(diff.diff_type for diff in diffs) if diffs else 'NO_CHANGE'}",
        source="soul reflect episode",
    )
    return patch_ids


def episode_messages(episode: dict[str, Any]) -> list[dict[str, Any]]:
    messages = episode.get("messages", [])
    if isinstance(messages, list):
        return [message for message in messages if isinstance(message, dict)]
    return []


def extract_cognitive_diffs(
    messages: list[dict[str, Any]],
    scope: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    max_patches: int = 3,
    rules: list[ReflectionRule] | None = None,
) -> list[CognitiveDiff]:
    active_rules = rules or load_reflection_rules()
    policy = load_reflection_policy()
    scope_data = json.loads(scope.get("data_json", "{}")) if scope and scope.get("data_json") else {}
    diffs: list[CognitiveDiff] = []
    seen: set[str] = set()

    for message_index, message in enumerate(messages, start=1):
        if message.get("role") != "user":
            continue
        for fragment in split_fragments(str(message.get("text") or message.get("content") or "")):
            if not scope_allows_fragment(fragment, scope_data):
                continue
            if not qualifies_for_patch_proposal(fragment, policy):
                continue
            matched_rule, matched_markers = match_fragment(fragment, active_rules)
            if matched_rule is None:
                continue
            content = compact_fragment(fragment)
            if content in seen:
                continue
            seen.add(content)
            diffs.append(
                CognitiveDiff(
                    diff_type="STATE_PATCH_PROPOSAL",
                    content=content,
                    operation="propose_patch",
                    requires_user_confirmation=True,
                    reason="Episode contains enough positive signal to propose a Current State patch.",
                    evidence={
                        "message_index": message_index,
                        "timestamp": message.get("timestamp"),
                        "matched_markers": matched_markers,
                        "matched_rule_type": matched_rule.patch_type,
                        "criteria": "positive_state_patch_signal",
                        "scope_id": scope.get("id") if scope else None,
                        "scope_name": scope.get("name") if scope else None,
                    },
                )
            )
            if len(diffs) >= max_patches:
                return diffs

    return diffs


def extract_artifact_paths(messages: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for message in messages:
        text = str(message.get("text") or message.get("content") or "")
        for match in ARTIFACT_PATTERN.finditer(text):
            path = match.group(1).replace("\\", "/").strip("./")
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def artifact_kind(path: str) -> str:
    suffix = PurePath(path).suffix.lower()
    if suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".ipynb", ".sql"}:
        return "code"
    if suffix in {".json", ".yaml", ".yml", ".toml"}:
        return "config"
    return "file"


def episode_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(message.get("text") or message.get("content") or "") for message in messages)


def load_reflection_rules(resource_name: str = DEFAULT_RULES_RESOURCE) -> list[ReflectionRule]:
    data = resources.files("soul.schemas").joinpath(resource_name).read_text(encoding="utf-8-sig")
    raw_rules = json.loads(data)
    return [
        ReflectionRule(
            patch_type=rule["type"],
            markers=tuple(rule.get("markers", [])),
        )
        for rule in raw_rules["rules"]
    ]


def load_reflection_policy(resource_name: str = DEFAULT_POLICY_RESOURCE) -> ReflectionPolicy:
    data = resources.files("soul.schemas").joinpath(resource_name).read_text(encoding="utf-8-sig")
    raw_policy = json.loads(data)
    min_fragment_length = raw_policy.get("min_fragment_length", 8)
    ignored_prefixes = raw_policy.get("ignored_prefixes", [])
    return ReflectionPolicy(
        min_fragment_length=int(min_fragment_length) if min_fragment_length is not None else 8,
        ignored_prefixes=tuple(ignored_prefixes if isinstance(ignored_prefixes, list) else []),
    )


def match_fragment(
    fragment: str,
    rules: list[ReflectionRule],
    min_length: int = 8,
) -> tuple[ReflectionRule | None, list[str]]:
    compact = " ".join(fragment.split()).lower()
    if len(compact) < min_length:
        return None, []

    for rule in rules:
        matched = [marker for marker in rule.markers if marker and marker.lower() in compact]
        if matched:
            return rule, matched
    return None, []


def qualifies_for_patch_proposal(fragment: str, policy: ReflectionPolicy | None = None) -> bool:
    active_policy = policy or load_reflection_policy()
    compact = " ".join(fragment.split()).lower()
    if len(compact) < active_policy.min_fragment_length:
        return False
    return not any(compact.startswith(prefix.lower()) for prefix in active_policy.ignored_prefixes)


def split_fragments(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").strip()
    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]
    if len(blocks) <= 1:
        return [normalized] if normalized else []
    return blocks


def compact_fragment(fragment: str, max_length: int = 500) -> str:
    compact = " ".join(fragment.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3].rstrip() + "..."


def scope_allows_fragment(fragment: str, scope_data: dict[str, Any]) -> bool:
    blocked = [str(marker).lower() for marker in scope_data.get("ignore_markers", [])]
    compact = fragment.lower()
    if any(marker and marker in compact for marker in blocked):
        return False
    required = [str(marker).lower() for marker in scope_data.get("cognitive_markers", [])]
    return not required or any(marker and marker in compact for marker in required)
