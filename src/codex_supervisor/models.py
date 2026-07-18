from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class DecisionKind(StrEnum):
    REPLY = "REPLY"
    HUMAN_REVIEW_NEEDED = "HUMAN_REVIEW_NEEDED"


@dataclass(frozen=True)
class ThreadCandidate:
    host_id: str
    thread_id: str
    title: str
    unread_at: int | None
    active_turn_id: str | None = None


@dataclass(frozen=True)
class TaskContext:
    candidate: ThreadCandidate
    recent_messages: tuple[str, ...]
    latest_visible_result: str | None


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    reply: str | None = None

    @classmethod
    def human_review(cls, reason: str) -> "Decision":
        return cls(DecisionKind.HUMAN_REVIEW_NEEDED, reason, None)

    def validate(self) -> "Decision":
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("decision reason is required")
        if self.kind is DecisionKind.REPLY and not (
            isinstance(self.reply, str) and self.reply.strip()
        ):
            raise ValueError("REPLY requires a non-empty reply")
        if self.kind is DecisionKind.HUMAN_REVIEW_NEEDED and self.reply is not None:
            raise ValueError("HUMAN_REVIEW_NEEDED requires reply: null")
        return self


@dataclass(frozen=True)
class ScanResult:
    candidates: tuple[ThreadCandidate, ...]
    unread_supported: bool
    note: str | None = None


@dataclass(frozen=True)
class DeliveryReceipt:
    """A transport acknowledgement; unread-state clearance is confirmed separately."""

    transport_accepted: bool
    delivery_id: str | None = None


@dataclass(frozen=True)
class SupervisorConfig:
    host_id: str
    state_path: Path
    socket_path: Path
    supervisor_thread_id: str | None = None


def text_from_item(item: Any) -> str | None:
    if isinstance(item, str):
        return item.strip() or None
    if not isinstance(item, dict):
        return None
    for key in ("text", "content", "message", "output"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
