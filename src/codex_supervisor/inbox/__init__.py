"""Fail-closed shared inbox integration for the local supervisor."""

from .config import InboxConfig, InboxMode
from .models import (
    ClaimedEnvelopeValidationError,
    InboxClaim,
    InboxDisposition,
    InboxEnvelope,
    InboxOutcome,
    MessageKind,
    SendReceipt,
)
from .protocol import InboxAdapter

__all__ = [
    "InboxAdapter",
    "ClaimedEnvelopeValidationError",
    "InboxClaim",
    "InboxConfig",
    "InboxDisposition",
    "InboxEnvelope",
    "InboxMode",
    "InboxOutcome",
    "MessageKind",
    "SendReceipt",
]
