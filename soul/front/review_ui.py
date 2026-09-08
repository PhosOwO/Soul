from __future__ import annotations

from importlib import resources


def render_review_page() -> str:
    return resources.files("soul.templates").joinpath("review_ui.html").read_text(encoding="utf-8")
