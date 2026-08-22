from __future__ import annotations

from soul.services.state_core.proposals import apply_patch_proposal, propose_patch
from soul.services.state_core.audit.state import audit_state
from soul.services.state_core.state_store import load_state, save_state


def test_state_audit_reports_review_status_in_current_state(tmp_path):
    state = load_state(tmp_path, project_name="Audit Test")
    proposal = propose_patch(
        state,
        {
            "source": "test",
            "summary": "Needs review item.",
            "state_item": {
                "id": "needs-review-item",
                "kind": "accepted_belief",
                "statement": "This item still needs review.",
                "status": "needs_review",
            },
        },
    )
    save_state(apply_patch_proposal(state, proposal, confirmed_by="test"), tmp_path)

    audit = audit_state(tmp_path)

    assert audit["summary"]["state_items"] == 3
    assert any(issue["code"] == "review_status_in_accepted_state" for issue in audit["issues"])


def test_state_audit_clean_initial_state_has_no_issues(tmp_path):
    load_state(tmp_path, project_name="Audit Test")

    audit = audit_state(tmp_path)

    assert audit["issues"] == []
