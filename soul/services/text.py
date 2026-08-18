from __future__ import annotations


def compact_text(text: object, max_length: int) -> str:
    one_line = " ".join(str(text or "").split())
    if len(one_line) <= max_length:
        return one_line
    return one_line[: max_length - 3].rstrip() + "..."


def compact_summary(text: object, max_length: int, *, fallback: str = "") -> str:
    summary = compact_text(text, max_length)
    return summary or fallback
