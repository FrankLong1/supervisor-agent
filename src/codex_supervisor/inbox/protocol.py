from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol

from .models import InboxClaim, InboxEnvelope, InboxOutcome, MessageKind, SendReceipt


class InboxAdapter(Protocol):
    adapter_identity: str
    contract_version: str

    def authenticated_identity(self) -> str: ...

    def list_deliveries(
        self, limit: int, status: str | None = None
    ) -> tuple[InboxEnvelope, ...]: ...

    def claim_next(
        self,
        instance_id: str,
        lease_seconds: int,
        recipient_agent_id: str | None = None,
    ) -> InboxClaim | None: ...

    def mark_received(self, delivery_id: str, instance_id: str) -> bool: ...

    def complete(
        self,
        delivery_id: str,
        instance_id: str,
        outcome: InboxOutcome,
        detail: Mapping[str, Any],
    ) -> bool: ...

    def send_message(
        self,
        *,
        sender_agent_id: str,
        recipient_address: str,
        kind: MessageKind,
        subject: str,
        body_text: str,
        body_json: Mapping[str, Any],
        idempotency_key: str,
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> SendReceipt: ...
