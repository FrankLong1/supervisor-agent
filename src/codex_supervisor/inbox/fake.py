from __future__ import annotations

from typing import Any, Mapping

from .models import InboxClaim, InboxEnvelope, InboxOutcome, SendReceipt


class FakeInboxAdapter:
    adapter_identity = "fake-stored-functions/v0"
    contract_version = "cloud-sql-agent-inbox-v0"

    def __init__(
        self, envelopes: tuple[InboxEnvelope, ...] = (), identity: str = "test_runtime"
    ):
        self.envelopes = list(envelopes)
        self.identity = identity
        self.claims: list[tuple[str, int, str | None]] = []
        self.received: list[tuple[str, str]] = []
        self.completions: list[tuple[str, str, InboxOutcome, Mapping[str, Any]]] = []
        self.sends: list[dict[str, Any]] = []
        self.claim: InboxClaim | None = None
        self.fail_at: str | None = None

    def authenticated_identity(self) -> str:
        if self.fail_at == "identity":
            raise RuntimeError("identity unavailable")
        return self.identity

    def list_deliveries(
        self, limit: int, status: str | None = None
    ) -> tuple[InboxEnvelope, ...]:
        if self.fail_at == "list":
            raise RuntimeError("list unavailable")
        return tuple(self.envelopes[:limit])

    def claim_next(
        self,
        instance_id: str,
        lease_seconds: int,
        recipient_agent_id: str | None = None,
    ) -> InboxClaim | None:
        self.claims.append((instance_id, lease_seconds, recipient_agent_id))
        if self.fail_at == "claim":
            raise RuntimeError("claim unavailable")
        return self.claim

    def mark_received(self, delivery_id: str, instance_id: str) -> bool:
        self.received.append((delivery_id, instance_id))
        if self.fail_at == "received":
            raise RuntimeError("receipt ambiguous")
        return True

    def complete(
        self,
        delivery_id: str,
        instance_id: str,
        outcome: InboxOutcome,
        detail: Mapping[str, Any],
    ) -> bool:
        self.completions.append((delivery_id, instance_id, outcome, detail))
        if self.fail_at == "complete":
            raise RuntimeError("completion ambiguous")
        return True

    def send_message(self, **kwargs: Any) -> SendReceipt:
        self.sends.append(dict(kwargs))
        if self.fail_at == "send":
            raise RuntimeError("send ambiguous")
        envelope = (
            self.claim.envelope
            if self.claim
            else (self.envelopes[0] if self.envelopes else None)
        )
        return SendReceipt(
            message_id="00000000-0000-0000-0000-000000000901",
            delivery_id="00000000-0000-0000-0000-000000000902",
            resolved_thread_id=(
                envelope.thread_id
                if envelope
                else "00000000-0000-0000-0000-000000000903"
            ),
            created=True,
        )
