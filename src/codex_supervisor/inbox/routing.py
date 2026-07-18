from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import InboxEnvelope, InboxOutcome, MessageKind


class InboxRoute(StrEnum):
    REFERENCE = "REFERENCE"
    DETERMINISTIC_REPLY = "DETERMINISTIC_REPLY"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    CORRELATE = "CORRELATE"
    SURFACE_RESULT = "SURFACE_RESULT"
    NOT_UNDERSTOOD = "NOT_UNDERSTOOD"


@dataclass(frozen=True)
class RoutingDecision:
    route: InboxRoute
    outcome: InboxOutcome
    reason: str
    reply: bool = False


def route_envelope(
    envelope: InboxEnvelope, *, explicit_canary: bool = False
) -> RoutingDecision:
    kind = envelope.kind
    if kind is MessageKind.NOTE:
        return RoutingDecision(
            InboxRoute.REFERENCE,
            InboxOutcome.RECEIVED_ONLY,
            "note recorded as reference",
        )
    if kind is MessageKind.QUESTION:
        if explicit_canary or envelope.allow_unattended_execution:
            return RoutingDecision(
                InboxRoute.DETERMINISTIC_REPLY,
                InboxOutcome.COMPLETED,
                "question eligible for bounded deterministic receipt",
                True,
            )
        return RoutingDecision(
            InboxRoute.HUMAN_REVIEW,
            InboxOutcome.NEEDS_HUMAN,
            "contact grant does not permit unattended handling",
        )
    if kind is MessageKind.TASK_PROPOSAL:
        return RoutingDecision(
            InboxRoute.HUMAN_REVIEW,
            InboxOutcome.NEEDS_HUMAN,
            "task proposal requires explicit recipient acceptance",
        )
    if kind is MessageKind.PROGRESS:
        return RoutingDecision(
            InboxRoute.CORRELATE,
            InboxOutcome.RECEIVED_ONLY,
            "progress is reference-only until a local correlation is known",
        )
    if kind is MessageKind.RESULT:
        return RoutingDecision(
            InboxRoute.SURFACE_RESULT,
            InboxOutcome.RECEIVED_ONLY,
            "result is surfaced without executing its content",
        )
    if kind is MessageKind.NEEDS_HUMAN:
        return RoutingDecision(
            InboxRoute.HUMAN_REVIEW,
            InboxOutcome.NEEDS_HUMAN,
            "sender explicitly requested human review",
        )
    if kind in {MessageKind.TASK_ACCEPTED, MessageKind.TASK_DECLINED}:
        return RoutingDecision(
            InboxRoute.REFERENCE,
            InboxOutcome.RECEIVED_ONLY,
            "task disposition recorded as reference",
        )
    return RoutingDecision(
        InboxRoute.NOT_UNDERSTOOD,
        InboxOutcome.NOT_UNDERSTOOD,
        "message kind has no executable handler",
    )
