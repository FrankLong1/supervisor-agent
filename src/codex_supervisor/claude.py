from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import Decision, DecisionKind, TaskContext
from .session import SessionAdapter

SYSTEM_PROMPT = Path(__file__).with_name("supervisor_prompt.md").read_text(encoding="utf-8")

DECISION_SCHEMA = json.dumps({
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "reason", "reply"],
    "properties": {
        "decision": {"type": "string", "enum": ["REPLY", "HUMAN_REVIEW_NEEDED"]},
        "reason": {"type": "string", "minLength": 1},
        "reply": {"type": ["string", "null"]},
    },
})


def parse_decision(payload: str) -> Decision:
    try:
        value = json.loads(payload)
        if not isinstance(value, dict) or set(value) != {"decision", "reason", "reply"}:
            raise ValueError("expected exactly decision, reason, and reply fields")
        return Decision(DecisionKind(value["decision"]), value["reason"], value["reply"]).validate()
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return Decision.human_review(f"invalid supervisor output: {error}")


class ConservativeClaude:
    """Adapter boundary used only to produce a recorded dry-run recommendation."""
    def __init__(self, session: SessionAdapter):
        self.session = session

    def decide(self, session_id: str | None, context: TaskContext) -> tuple[str, Decision]:
        returned_id, output = self.session.decide(session_id, context, SYSTEM_PROMPT)
        if not isinstance(returned_id, str) or not returned_id.strip():
            return session_id or "invalid-session", Decision.human_review("Claude/Fable adapter returned no stable session ID")
        return returned_id, parse_decision(output)


class ClaudeCodeSession:
    """Verified local Claude Code/Fable CLI adapter with a persisted session."""

    def __init__(self, command: str, model: str, timeout_seconds: float = 120.0):
        self.command, self.model, self.timeout_seconds = command, model, timeout_seconds

    def decide(self, session_id: str | None, context: TaskContext, system_prompt: str) -> tuple[str, str]:
        prompt = json.dumps({
            "source_thread_id": context.candidate.thread_id,
            "source_title": context.candidate.title,
            "recent_messages": context.recent_messages,
            "latest_visible_result": context.latest_visible_result,
        })
        # Reassert the file-backed policy on resumed sessions too, so an
        # operator's prompt edit affects the next decision rather than only a
        # newly-created Fable session.
        command = [self.command, "-p", "--model", self.model, "--output-format", "json", "--json-schema", DECISION_SCHEMA, "--permission-mode", "dontAsk", "--tools", "", "--system-prompt", system_prompt]
        if session_id:
            command.extend(["--resume", session_id])
        command.extend(["--", prompt])
        completed = subprocess.run(command, check=False, text=True, capture_output=True, timeout=self.timeout_seconds)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"Fable exited {completed.returncode}")
        try:
            result = json.loads(completed.stdout)
            returned_id, output = result["session_id"], result["result"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"invalid Fable CLI output: {error}") from error
        if not isinstance(returned_id, str) or not isinstance(output, str):
            raise RuntimeError("Fable CLI did not return a session ID and text result")
        return returned_id, output
