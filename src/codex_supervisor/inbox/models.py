from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
from typing import Any, Mapping
from uuid import UUID


MAX_SUBJECT_BYTES = 500
MAX_BODY_TEXT_BYTES = 64 * 1024
MAX_BODY_JSON_BYTES = 64 * 1024
MAX_METADATA_BYTES = 512


class InboxValidationError(ValueError):
    """A bounded, safe-to-display shared-contract validation failure."""


@dataclass(frozen=True)
class InvalidInboxClaim:
    delivery_id: str
    message_id: str
    thread_id: str
    recipient_agent_id: str
    claimant_instance_id: str
    claim_until: datetime
    reason: str


class ClaimedEnvelopeValidationError(InboxValidationError):
    """A live claim whose safe correlation fields remain attributable."""

    def __init__(self, claim: InvalidInboxClaim):
        super().__init__(claim.reason)
        self.claim = claim

    @classmethod
    def from_row(
        cls, row: Mapping[str, Any], error: InboxValidationError
    ) -> "ClaimedEnvelopeValidationError":
        return cls(
            InvalidInboxClaim(
                delivery_id=_uuid(row.get("delivery_id"), "delivery_id"),
                message_id=_uuid(row.get("message_id"), "message_id"),
                thread_id=_uuid(row.get("thread_id"), "thread_id"),
                recipient_agent_id=_uuid(
                    row.get("recipient_agent_id"), "recipient_agent_id"
                ),
                claimant_instance_id=_text(
                    row.get("claimed_by"), "claimed_by", 200, empty=False
                ),
                claim_until=_timestamp(row.get("claim_until"), "claim_until"),
                reason=f"claimed envelope validation failed: {str(error)[:256]}",
            )
        )


class MessageKind(StrEnum):
    NOTE = "NOTE"
    QUESTION = "QUESTION"
    TASK_PROPOSAL = "TASK_PROPOSAL"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    TASK_DECLINED = "TASK_DECLINED"
    PROGRESS = "PROGRESS"
    RESULT = "RESULT"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    NOT_UNDERSTOOD = "NOT_UNDERSTOOD"


class InboxOutcome(StrEnum):
    RECEIVED_ONLY = "RECEIVED_ONLY"
    COMPLETED = "COMPLETED"
    DECLINED = "DECLINED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    NOT_UNDERSTOOD = "NOT_UNDERSTOOD"


def _uuid(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise InboxValidationError(f"invalid {field}") from error


def _timestamp(value: Any, field: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise InboxValidationError(f"invalid {field}") from error
    else:
        raise InboxValidationError(f"invalid {field}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InboxValidationError(f"{field} must include a timezone")
    return parsed


def _text(value: Any, field: str, limit: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise InboxValidationError(f"invalid {field}")
    if len(value.encode("utf-8")) > limit:
        raise InboxValidationError(f"{field} exceeds {limit} bytes")
    return value


def _json_object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise InboxValidationError("body_json must be an object")
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError) as error:
        raise InboxValidationError("body_json is not valid JSON") from error
    if len(encoded.encode("utf-8")) > MAX_BODY_JSON_BYTES:
        raise InboxValidationError(f"body_json exceeds {MAX_BODY_JSON_BYTES} bytes")
    return dict(value)


@dataclass(frozen=True)
class InboxEnvelope:
    message_id: str
    delivery_id: str
    thread_id: str
    reply_to_message_id: str | None
    sender_principal_id: str
    sender_agent_id: str
    sender_address: str
    sender_display_name: str
    recipient_agent_id: str
    kind: MessageKind
    subject: str
    body_text: str
    body_json: Mapping[str, Any]
    created_at: datetime
    expires_at: datetime | None
    allow_unattended_execution: bool = False

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "InboxEnvelope":
        required = {
            "message_id",
            "delivery_id",
            "thread_id",
            "reply_to_message_id",
            "sender_principal_id",
            "sender_agent_id",
            "sender_address",
            "sender_display_name",
            "recipient_agent_id",
            "kind",
            "subject",
            "body_text",
            "body_json",
            "created_at",
            "expires_at",
        }
        missing = sorted(required.difference(row))
        if missing:
            raise InboxValidationError(f"missing envelope fields: {', '.join(missing)}")
        try:
            kind = MessageKind(str(row["kind"]))
        except ValueError as error:
            raise InboxValidationError("unsupported message kind") from error
        unattended = row.get("allow_unattended_execution", False)
        if not isinstance(unattended, bool):
            raise InboxValidationError("invalid allow_unattended_execution")
        return cls(
            message_id=_uuid(row["message_id"], "message_id"),
            delivery_id=_uuid(row["delivery_id"], "delivery_id"),
            thread_id=_uuid(row["thread_id"], "thread_id"),
            reply_to_message_id=_uuid(
                row["reply_to_message_id"], "reply_to_message_id", optional=True
            ),
            sender_principal_id=_uuid(
                row["sender_principal_id"], "sender_principal_id"
            ),
            sender_agent_id=_uuid(row["sender_agent_id"], "sender_agent_id"),
            sender_address=_text(
                row["sender_address"], "sender_address", MAX_METADATA_BYTES, empty=False
            ),
            sender_display_name=_text(
                row["sender_display_name"], "sender_display_name", MAX_METADATA_BYTES
            ),
            recipient_agent_id=_uuid(row["recipient_agent_id"], "recipient_agent_id"),
            kind=kind,
            subject=_text(row["subject"], "subject", MAX_SUBJECT_BYTES),
            body_text=_text(row["body_text"], "body_text", MAX_BODY_TEXT_BYTES),
            body_json=_json_object(row["body_json"]),
            created_at=_timestamp(row["created_at"], "created_at"),
            expires_at=_timestamp(row["expires_at"], "expires_at", optional=True),
            allow_unattended_execution=unattended,
        )


@dataclass(frozen=True)
class InboxClaim:
    envelope: InboxEnvelope
    claimant_instance_id: str
    claimed_at: datetime
    claim_until: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "InboxClaim":
        envelope = InboxEnvelope.from_row(row)
        instance = _text(
            row.get("claimed_by"), "claimed_by", MAX_METADATA_BYTES, empty=False
        )
        claimed_at = _timestamp(row.get("claimed_at"), "claimed_at")
        claim_until = _timestamp(row.get("claim_until"), "claim_until")
        if claim_until <= claimed_at:
            raise InboxValidationError("claim_until must be after claimed_at")
        return cls(envelope, instance, claimed_at, claim_until)


@dataclass(frozen=True)
class InboxDisposition:
    outcome: InboxOutcome
    reason: str
    reply_kind: MessageKind | None = None
    reply_subject: str | None = None
    reply_body_text: str | None = None
    reply_body_json: Mapping[str, Any] | None = None
    reply_idempotency_key: str | None = None


@dataclass(frozen=True)
class SendReceipt:
    message_id: str
    delivery_id: str
    resolved_thread_id: str
    created: bool

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SendReceipt":
        created = row.get("created")
        if not isinstance(created, bool):
            raise InboxValidationError("invalid send receipt created flag")
        return cls(
            _uuid(row.get("message_id"), "message_id"),
            _uuid(row.get("delivery_id"), "delivery_id"),
            _uuid(row.get("resolved_thread_id"), "resolved_thread_id"),
            created,
        )
