from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class StateItem(TypedDict):
    id: str
    kind: str
    statement: str
    status: NotRequired[str]
    priority: NotRequired[str]
    confidence: NotRequired[float]
    evidence_count: NotRequired[int]
    latest_evidence: NotRequired[str]
    why_remember: NotRequired[str]
    ttl_turns: NotRequired[int]
    created_at: NotRequired[str]
    updated_at: NotRequired[str]
    created_version: NotRequired[int]


class CurrentState(TypedDict):
    beliefs: list[dict[str, Any]]
    constraints: list[str]
    open_questions: list[str]
    state_items: list[StateItem]


class StateHistoryEntry(TypedDict):
    proposal_id: str
    confirmed_by: str
    applied_at: str
    operations: list["PatchOperation"]


class StateDoc(TypedDict):
    schema_version: int
    project: str
    version: int
    updated_at: str
    current_state: CurrentState
    history: list[StateHistoryEntry]


class EvidencePayload(TypedDict, total=False):
    source: str
    task: str
    summary: str
    content: str
    title: str
    why_remember: str
    memory_owner: str
    state_owner: str
    evidence_refs: list[dict[str, Any]]
    reme: dict[str, Any]
    knowledge_points: list["KnowledgePoint"]
    operations: list["PatchOperation"]
    state_item: StateItem
    state_items: list[StateItem]


class KnowledgePoint(TypedDict):
    id: str
    kind: str
    statement: str
    status: NotRequired[str]
    priority: NotRequired[str]
    confidence: NotRequired[float]
    why_remember: NotRequired[str]
    ttl_turns: NotRequired[int]


class PatchOperation(TypedDict):
    op: str
    id: str
    value: Any
    evidence: dict[str, Any]


class ProposalRefs(TypedDict, total=False):
    evidence_refs: list[dict[str, Any]]
    reme: dict[str, Any]
    raw_evidence: dict[str, Any]


class PatchProposal(TypedDict):
    id: str
    created_at: str
    updated_at: NotRequired[str]
    updated_by: NotRequired[str]
    revision_of: NotRequired[str]
    status: str
    status_reason: NotRequired[str]
    title: str
    knowledge_points: list[KnowledgePoint]
    why_remember: str
    refs: ProposalRefs
    source: str
    base_version: int
    evidence: dict[str, Any]
    operations: list[PatchOperation]
    review_recommendation: str
