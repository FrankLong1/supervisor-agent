from __future__ import annotations

import json
from typing import Protocol

from .models import Decision, DecisionKind, TaskContext

SYSTEM_PROMPT = """You supervise one Codex task at a time. Return exactly one JSON object with decision, reason, and reply. Allowed decisions are REPLY and HUMAN_REVIEW_NEEDED. Choose HUMAN_REVIEW_NEEDED when work is complete, context is insufficient, human approval is required, risk is unclear, or the next action is not obvious. Never request more transcript. For HUMAN_REVIEW_NEEDED reply must be null."""


class ClaudeSession(Protocol):
    def decide(self, session_id: str | None, context: TaskContext) -> tuple[str, str]: ...


def parse_decision(payload: str) -> Decision:
    try:
        value = json.loads(payload)
        if not isinstance(value, dict) or set(value) != {"decision", "reason", "reply"}:
            raise ValueError("expected exactly decision, reason, and reply fields")
        return Decision(DecisionKind(value["decision"]), value["reason"], value["reply"]).validate()
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return Decision.human_review(f"invalid supervisor output: {error}")


class ConservativeClaude:
    """Adapter boundary; no production Claude/Fable command is enabled by this project."""
    def __init__(self, session: ClaudeSession):
        self.session = session

    def decide(self, session_id: str | None, context: TaskContext) -> tuple[str, Decision]:
        returned_id, output = self.session.decide(session_id, context)
        if not isinstance(returned_id, str) or not returned_id.strip():
            return session_id or "invalid-session", Decision.human_review("Claude/Fable adapter returned no stable session ID")
        return returned_id, parse_decision(output)
