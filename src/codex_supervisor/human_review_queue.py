from __future__ import annotations

from collections.abc import Iterable


def _cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render_markdown(rows: Iterable[dict[str, str]]) -> str:
    """Render the console-compatible, read-only human-review queue."""
    lines = ["# Human review", "", "| Marked at | Task | Reason | Task ID |", "| --- | --- | --- | --- |"]
    for row in rows:
        title = _cell(row["title"] or "Untitled task")
        reason = _cell(row["reason"])
        thread_id = row["thread_id"]
        lines.append(f"| {row['marked_at']} | {title} | {reason} | `{thread_id}` |")
    if len(lines) == 4:
        lines.extend(["", "No tasks currently need human review."])
    return "\n".join(lines)
