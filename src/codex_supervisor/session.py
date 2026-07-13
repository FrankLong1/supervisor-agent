from __future__ import annotations

from typing import Protocol

from .models import TaskContext


class SessionAdapter(Protocol):
    """Provider-neutral, resumable decision session used by the supervisor."""

    def decide(self, session_id: str | None, context: TaskContext, system_prompt: str) -> tuple[str, str]: ...
