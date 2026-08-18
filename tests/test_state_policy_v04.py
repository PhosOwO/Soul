from __future__ import annotations

from soul.services.state import (
    apply_patch_proposal,
    format_state_context,
    initial_state,
    project_state_items,
    propose_patch,
)


def test_explicit_state_items_become_patch_operations() -> None:
    state = initial_state("Policy Test")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "User confirmed one constraint and one rejected direction.",
            "state_items": [
                {
                    "id": "confirmed-scope",
                    "kind": "active_constraint",
                    "statement": "Stay within the confirmed experiment scope.",
                    "priority": "high",
                    "confidence": 0.9,
                },
                {
                    "id": "discarded-route",
                    "kind": "rejected_direction",
                    "statement": "Do not repeat the discarded route.",
                    "priority": "high",
                    "confidence": 0.8,
                },
            ],
        },
    )

    kinds = {operation["value"]["kind"] for operation in proposal["operations"] if operation["op"] == "upsert_state_item"}

    assert proposal["review_recommendation"] == "auto_accept"
    assert "rejected_direction" in kinds
    assert "active_constraint" in kinds


def test_policy_projection_prioritizes_constraints_and_gates() -> None:
    state = initial_state("Projection Test")
    for item in [
        {
            "id": "scope-constraint",
            "kind": "active_constraint",
            "statement": "Stay within the current confirmed scope.",
            "priority": "high",
            "confidence": 0.9,
        },
        {
            "id": "quality-gate",
            "kind": "decision_gate",
            "statement": "Any proposed improvement must pass the quality gate.",
            "priority": "high",
            "confidence": 0.88,
        },
        {
            "id": "temporary-idea",
            "kind": "open_question",
            "statement": "Check whether a temporary idea should be explored later.",
            "status": "needs_review",
            "priority": "low",
            "confidence": 0.45,
        },
    ]:
        proposal = propose_patch(state, {"source": "test", "summary": item["statement"], "state_item": item})
        state = apply_patch_proposal(state, proposal, confirmed_by="test")

    projection = project_state_items(state, task="current scope quality gate", limit=3)
    context = format_state_context(state, task="current scope quality gate", limit=3)

    assert "active_constraint" in [item["kind"] for item in projection]
    assert "decision_gate" in [item["kind"] for item in projection]
    assert "confirmed scope" in context
    assert "quality gate" in context
    assert "temporary idea" not in context


def test_unclassified_evidence_does_not_enter_state_projection() -> None:
    state = initial_state("Review Test")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "A new note arrived, but no classifier decided whether it should change accepted state.",
            "content": "",
        },
    )

    state_item_ops = [operation for operation in proposal["operations"] if operation["op"] == "upsert_state_item"]

    assert proposal["review_recommendation"] == "reject"
    assert proposal["knowledge_points"] == []
    assert state_item_ops == []


def test_durable_evidence_becomes_readable_knowledge_points() -> None:
    state = initial_state("Review Test")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": (
                "ERA5 accumulated-like heat flux files should be converted to W/m2 by dividing by 3600. "
                "Created a temporary validation script."
            ),
        },
    )

    state_item_ops = [operation for operation in proposal["operations"] if operation["op"] == "upsert_state_item"]

    assert proposal["title"]
    assert proposal["why_remember"]
    assert proposal["knowledge_points"][0]["statement"].startswith("ERA5 accumulated-like heat flux")
    assert "Created a temporary validation script" not in proposal["knowledge_points"][0]["statement"]
    assert state_item_ops[0]["value"]["kind"] == "active_constraint"
