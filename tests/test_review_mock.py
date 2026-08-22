from __future__ import annotations

import pytest

from soul.services.state_core.review.card import build_review_card
from soul.services.state_core.review.mock import create_review_mock_project
from soul.services.state_core.state_store import load_state


def test_review_mock_creates_sandbox_review_candidates(tmp_path):
    target = tmp_path / "review-mock"

    result = create_review_mock_project(target, reset=True)
    card = build_review_card(target)

    assert result["project_dir"] == str(target)
    assert card["counts"]["ready_to_confirm"] == 2
    assert card["counts"]["needs_review"] == 1
    assert not (tmp_path / ".soul").exists()


def test_review_mock_refuses_to_reset_current_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_state(tmp_path, project_name="Real Project")

    with pytest.raises(ValueError, match="Refusing to reset"):
        create_review_mock_project(tmp_path, reset=True)
