from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any


@dataclass(frozen=True, slots=True)
class StateProjectionPolicy:
    state_kind_order: tuple[str, ...]
    priority_score: dict[str, int]
    durable_knowledge_keywords: tuple[str, ...]
    episodic_prefixes: tuple[str, ...]
    meta_review_phrases: tuple[str, ...]
    kind_rules: tuple[dict[str, Any], ...]
    high_priority_keywords: tuple[str, ...]
    why_remember_rules: tuple[dict[str, Any], ...]
    default_why_remember: str

    def is_meta_review(self, lowered_sentence: str) -> bool:
        return contains_any(lowered_sentence, self.meta_review_phrases)

    def is_episodic_prefix(self, lowered_sentence: str) -> bool:
        return lowered_sentence.startswith(self.episodic_prefixes)

    def contains_durable_signal(self, lowered_sentence: str) -> bool:
        return contains_any(lowered_sentence, self.durable_knowledge_keywords)

    def infer_kind(self, statement: str) -> str:
        lowered = statement.lower()
        for rule in self.kind_rules:
            keywords = tuple(str(keyword) for keyword in rule.get("keywords", []))
            if contains_any(lowered, keywords):
                return str(rule.get("kind") or "accepted_belief")
        return "accepted_belief"

    def infer_priority(self, statement: str) -> str:
        return "high" if contains_any(statement.lower(), self.high_priority_keywords) else "medium"

    def kind_order(self, kind: str) -> int:
        try:
            return self.state_kind_order.index(kind)
        except ValueError:
            return 99

    def priority_rank(self, priority: str) -> int:
        return int(self.priority_score.get(priority, self.priority_score.get("medium", 2)))

    def why_remember(self, statement: str) -> str:
        lowered = statement.lower()
        for rule in self.why_remember_rules:
            keywords = tuple(str(keyword) for keyword in rule.get("keywords", []))
            if contains_any(lowered, keywords):
                return str(rule.get("message") or self.default_why_remember)
        return self.default_why_remember


def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


@lru_cache(maxsize=1)
def load_state_projection_policy() -> StateProjectionPolicy:
    resource = files("soul.policies").joinpath("state_projection.json")
    data = json.loads(resource.read_text(encoding="utf-8"))
    return StateProjectionPolicy(
        state_kind_order=tuple(str(value) for value in data.get("state_kind_order", [])),
        priority_score={
            str(key): int(value)
            for key, value in data.get("priority_score", {}).items()
            if isinstance(value, int)
        },
        durable_knowledge_keywords=tuple(str(value) for value in data.get("durable_knowledge_keywords", [])),
        episodic_prefixes=tuple(str(value) for value in data.get("episodic_prefixes", [])),
        meta_review_phrases=tuple(str(value) for value in data.get("meta_review_phrases", [])),
        kind_rules=tuple(rule for rule in data.get("kind_rules", []) if isinstance(rule, dict)),
        high_priority_keywords=tuple(str(value) for value in data.get("high_priority_keywords", [])),
        why_remember_rules=tuple(rule for rule in data.get("why_remember_rules", []) if isinstance(rule, dict)),
        default_why_remember=str(
            data.get("default_why_remember") or "This may be reusable project knowledge for future turns."
        ),
    )
