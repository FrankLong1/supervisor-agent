from __future__ import annotations

import hashlib
from typing import Any

from ..state import SupervisorState
from .config import InboxConfig, InboxMode
from .models import (
    ClaimedEnvelopeValidationError,
    InboxClaim,
    InboxOutcome,
    MessageKind,
)
from .protocol import InboxAdapter
from .routing import InboxRoute, route_envelope


HANDLER_IDENTITY = "deterministic-inbox-handler/v0"
CANARY_EVIDENCE_ID = "synthetic-question-reply/v0"


class InboxService:
    def __init__(
        self, config: InboxConfig, adapter: InboxAdapter, state: SupervisorState
    ):
        self.config = config
        self.adapter = adapter
        self.state = state

    def scan_once(self, limit: int = 20) -> dict[str, Any]:
        if self.config.mode is InboxMode.DISABLED:
            raise ValueError("inbox is disabled")
        envelopes = self.adapter.list_deliveries(limit, "QUEUED")
        routes: dict[str, int] = {}
        items: list[dict[str, str]] = []
        for envelope in envelopes:
            decision = route_envelope(envelope)
            self.state.record_inbox_observation(
                delivery_id=envelope.delivery_id,
                message_id=envelope.message_id,
                thread_id=envelope.thread_id,
                kind=envelope.kind.value,
                route=decision.route.value,
                disposition=decision.outcome.value,
                reason=decision.reason,
            )
            routes[decision.route.value] = routes.get(decision.route.value, 0) + 1
            items.append(
                {
                    "delivery_id": envelope.delivery_id,
                    "message_id": envelope.message_id,
                    "thread_id": envelope.thread_id,
                    "kind": envelope.kind.value,
                    "route": decision.route.value,
                    "proposed_disposition": decision.outcome.value,
                    "reason": decision.reason,
                }
            )
        self.state.record_inbox_poll()
        return {
            "read_only": True,
            "observed": len(envelopes),
            "routes": routes,
            "deliveries": items,
        }

    def canary(
        self, expected_delivery_id: str, sender_observed_reply_id: str | None = None
    ) -> dict[str, Any]:
        self.config.require_live_identity()
        principal_identity = self._identity()
        existing = self.state.inbox_processing(expected_delivery_id)
        if sender_observed_reply_id is not None:
            if existing is None or existing["local_status"] != "REPLIED":
                raise ValueError(
                    "canary delivery does not have a completed local reply"
                )
            if existing["reply_message_id"] != sender_observed_reply_id:
                raise ValueError(
                    "sender-observed reply does not match the stored reply ID"
                )
            self.state.record_inbox_canary(
                evidence_id=CANARY_EVIDENCE_ID,
                contract_version=self.adapter.contract_version,
                adapter_identity=self.adapter.adapter_identity,
                principal_identity=principal_identity,
                instance_id=self.config.instance_id,
                handler_identity=HANDLER_IDENTITY,
                delivery_id=expected_delivery_id,
                reply_message_id=sender_observed_reply_id,
            )
            return {
                "canary_enrolled": True,
                "delivery_id": expected_delivery_id,
                "reply_message_id": sender_observed_reply_id,
            }
        if existing is not None:
            reply_message_id = existing["reply_message_id"]
            enrolled = bool(
                existing["local_status"] == "REPLIED"
                and reply_message_id
                and self.state.inbox_canary_delivery_matches(
                    evidence_id=CANARY_EVIDENCE_ID,
                    contract_version=self.adapter.contract_version,
                    adapter_identity=self.adapter.adapter_identity,
                    principal_identity=principal_identity,
                    instance_id=self.config.instance_id,
                    handler_identity=HANDLER_IDENTITY,
                    delivery_id=expected_delivery_id,
                    reply_message_id=reply_message_id,
                )
            )
            return {
                "canary_enrolled": enrolled,
                "delivery_id": expected_delivery_id,
                "local_status": existing["local_status"],
                "reply_message_id": reply_message_id,
                "sender_verification_required": (
                    existing["local_status"] == "REPLIED" and not enrolled
                ),
            }
        queued = self.adapter.list_deliveries(1, "QUEUED")
        if len(queued) != 1 or queued[0].delivery_id != expected_delivery_id:
            raise ValueError(
                "expected canary must be the single oldest eligible delivery"
            )
        envelope = queued[0]
        if envelope.recipient_agent_id != self.config.agent_id:
            raise ValueError(
                "canary recipient does not match configured agent identity"
            )
        if (
            envelope.kind is not MessageKind.QUESTION
            or envelope.body_json.get("supervisor_canary") is not True
        ):
            raise ValueError("delivery is not an explicit synthetic QUESTION canary")
        result = self._claim_and_handle(
            expected_delivery_id=expected_delivery_id, explicit_canary=True
        )
        result["canary_enrolled"] = False
        result["sender_verification_required"] = result.get("local_status") == "REPLIED"
        return result

    def run_once(self) -> dict[str, Any]:
        self.config.require_live_identity()
        principal_identity = self._identity()
        if not self.state.inbox_canary_matches(
            evidence_id=CANARY_EVIDENCE_ID,
            contract_version=self.adapter.contract_version,
            adapter_identity=self.adapter.adapter_identity,
            principal_identity=principal_identity,
            instance_id=self.config.instance_id,
            handler_identity=HANDLER_IDENTITY,
        ):
            raise ValueError(
                "matching sender-verified inbox canary evidence is required"
            )
        return self._claim_and_handle()

    def poll_once(self, limit: int = 20) -> dict[str, Any]:
        """Observe the queue every tick and handle at most one canary-cleared item."""
        if self.config.mode is not InboxMode.POLL:
            raise ValueError("persistent inbox polling requires poll mode")
        self.config.require_live_identity()
        observed = self.scan_once(limit)
        principal_identity = self._identity()
        canary_matches = self.state.inbox_canary_matches(
            evidence_id=CANARY_EVIDENCE_ID,
            contract_version=self.adapter.contract_version,
            adapter_identity=self.adapter.adapter_identity,
            principal_identity=principal_identity,
            instance_id=self.config.instance_id,
            handler_identity=HANDLER_IDENTITY,
        )
        if not canary_matches:
            return {
                **observed,
                "mode": self.config.mode.value,
                "handling_enabled": False,
                "claimed": False,
                "reason": "matching sender-verified inbox canary evidence is required",
            }
        return {
            **observed,
            "mode": self.config.mode.value,
            "handling_enabled": True,
            **self._claim_and_handle(),
        }

    def send_task(
        self,
        *,
        recipient_address: str,
        subject: str,
        body_text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.config.require_live_identity()
        authenticated_identity = self._identity()
        sender_address = (self.config.agent_address or "").strip()
        recipient_address = recipient_address.strip()
        subject = subject.strip()
        if not sender_address:
            raise ValueError("sending tasks requires SUPERVISOR_INBOX_AGENT_ADDRESS")
        if not recipient_address or len(recipient_address) > 320:
            raise ValueError("recipient address must be between 1 and 320 characters")
        if recipient_address.casefold() == sender_address.casefold():
            raise ValueError(
                "refusing to send a shared inbox task to the configured sender"
            )
        if not subject or len(subject) > 500:
            raise ValueError("task subject must be between 1 and 500 characters")
        if not body_text.strip() or len(body_text.encode("utf-8")) > 65536:
            raise ValueError("task body must be non-empty and at most 65536 bytes")
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("idempotency key must be between 1 and 200 characters")
        receipt = self.adapter.send_message(
            sender_agent_id=self.config.agent_id,
            recipient_address=recipient_address,
            kind=MessageKind.TASK_PROPOSAL,
            subject=subject,
            body_text=body_text,
            body_json={
                "format": "shared-inbox-task/v0",
                "sender_agent_address": sender_address,
            },
            idempotency_key=idempotency_key,
        )
        return {
            "queued": True,
            "created": receipt.created,
            "kind": MessageKind.TASK_PROPOSAL.value,
            "authenticated_identity": authenticated_identity,
            "sender_agent_id": self.config.agent_id,
            "sender_address": sender_address,
            "recipient_address": recipient_address,
            "message_id": receipt.message_id,
            "delivery_id": receipt.delivery_id,
            "thread_id": receipt.resolved_thread_id,
            "accepted_by_recipient": False,
        }

    def _identity(self) -> str:
        identity = self.adapter.authenticated_identity()
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("database runtime identity is not attributable")
        return identity

    @staticmethod
    def _reply_key(delivery_id: str) -> str:
        digest = hashlib.sha256(
            f"{HANDLER_IDENTITY}:{delivery_id}".encode()
        ).hexdigest()
        return f"supervisor-inbox-v0:{digest}"

    def _claim_and_handle(
        self, *, expected_delivery_id: str | None = None, explicit_canary: bool = False
    ) -> dict[str, Any]:
        # Identity and canary checks happen immediately before this atomic claim.
        try:
            claim = self.adapter.claim_next(
                self.config.instance_id,
                self.config.claim_seconds,
                self.config.agent_id,
            )
        except ClaimedEnvelopeValidationError as error:
            return self._handle_invalid_claim(error)
        if claim is None:
            return {"claimed": False, "local_status": None}
        envelope = claim.envelope
        if claim.claimant_instance_id != self.config.instance_id:
            return self._ambiguous_unpersisted(
                claim, "ambiguous claimant identity returned after claim"
            )
        if envelope.recipient_agent_id != self.config.agent_id:
            return self._ambiguous_unpersisted(
                claim, "ambiguous recipient identity after claim"
            )
        if (
            expected_delivery_id is not None
            and envelope.delivery_id != expected_delivery_id
        ):
            return self._ambiguous_unpersisted(
                claim, "ambiguous delivery selected after canary preflight"
            )
        if not self.state.begin_inbox_processing(claim, HANDLER_IDENTITY):
            self.state.mark_inbox_human_review(
                self.config.instance_id,
                envelope.delivery_id,
                envelope.thread_id,
                "ambiguous duplicate local processing row after shared claim",
            )
            return {
                "claimed": True,
                "delivery_id": envelope.delivery_id,
                "local_status": "AMBIGUOUS",
            }
        try:
            received = self.adapter.mark_received(
                envelope.delivery_id, self.config.instance_id
            )
        except Exception:
            return self._ambiguous(
                claim, "ambiguous mark-received outcome after transport error"
            )
        if not received:
            return self._ambiguous(claim, "ambiguous mark-received rejection")
        self.state.update_inbox_processing(envelope.delivery_id, "RECEIVED")
        decision = route_envelope(envelope, explicit_canary=explicit_canary)
        if decision.route is InboxRoute.HUMAN_REVIEW:
            if not self._complete(claim, decision.outcome, decision.reason):
                return self._ambiguous(
                    claim, "ambiguous human-review completion outcome"
                )
            self.state.update_inbox_processing(
                envelope.delivery_id,
                "NEEDS_HUMAN",
                proposed_outcome=decision.outcome.value,
            )
            self.state.mark_inbox_human_review(
                self.config.instance_id,
                envelope.delivery_id,
                envelope.thread_id,
                decision.reason,
                envelope.subject or "Shared inbox delivery",
            )
            return {
                "claimed": True,
                "delivery_id": envelope.delivery_id,
                "local_status": "NEEDS_HUMAN",
                "outcome": decision.outcome.value,
            }
        if not decision.reply:
            self.state.update_inbox_processing(
                envelope.delivery_id,
                "RECEIVED",
                proposed_outcome=decision.outcome.value,
            )
            if not self._complete(claim, decision.outcome, decision.reason):
                return self._ambiguous(claim, "ambiguous completion outcome")
            self.state.update_inbox_processing(envelope.delivery_id, "HANDLED")
            return {
                "claimed": True,
                "delivery_id": envelope.delivery_id,
                "local_status": "HANDLED",
                "outcome": decision.outcome.value,
            }
        key = self._reply_key(envelope.delivery_id)
        self.state.update_inbox_processing(
            envelope.delivery_id,
            "RECEIVED",
            proposed_outcome=decision.outcome.value,
            reply_idempotency_key=key,
        )
        body = (
            f"Synthetic inbox canary received for delivery {envelope.delivery_id}."
            if explicit_canary
            else f"Deterministic receipt for question delivery {envelope.delivery_id}; no generic agent execution was performed."
        )
        try:
            receipt = self.adapter.send_message(
                sender_agent_id=self.config.agent_id,
                recipient_address=envelope.sender_address,
                kind=MessageKind.RESULT,
                subject="Inbox receipt",
                body_text=body,
                body_json={
                    "handler": HANDLER_IDENTITY,
                    "source_delivery_id": envelope.delivery_id,
                },
                idempotency_key=key,
                thread_id=envelope.thread_id,
                reply_to_message_id=envelope.message_id,
            )
        except Exception:
            return self._ambiguous(
                claim, "ambiguous reply send outcome; automatic retry is disabled"
            )
        if receipt.resolved_thread_id != envelope.thread_id:
            return self._ambiguous(claim, "ambiguous reply thread returned by database")
        self.state.update_inbox_processing(
            envelope.delivery_id, "RECEIVED", reply_message_id=receipt.message_id
        )
        if not self._complete(claim, decision.outcome, decision.reason):
            return self._ambiguous(claim, "ambiguous completion after durable reply ID")
        self.state.update_inbox_processing(envelope.delivery_id, "REPLIED")
        return {
            "claimed": True,
            "delivery_id": envelope.delivery_id,
            "reply_message_id": receipt.message_id,
            "reply_created": receipt.created,
            "local_status": "REPLIED",
            "outcome": decision.outcome.value,
        }

    def _complete(self, claim: InboxClaim, outcome: InboxOutcome, reason: str) -> bool:
        try:
            return self.adapter.complete(
                claim.envelope.delivery_id,
                self.config.instance_id,
                outcome,
                {"handler": HANDLER_IDENTITY, "reason": reason[:512]},
            )
        except Exception:
            return False

    def _ambiguous_unpersisted(self, claim: InboxClaim, reason: str) -> dict[str, Any]:
        self.state.begin_inbox_processing(claim, HANDLER_IDENTITY)
        return self._ambiguous(claim, reason)

    def _handle_invalid_claim(
        self, error: ClaimedEnvelopeValidationError
    ) -> dict[str, Any]:
        claim = error.claim
        self.state.begin_inbox_processing_reference(
            delivery_id=claim.delivery_id,
            message_id=claim.message_id,
            thread_id=claim.thread_id,
            claimant_instance_id=claim.claimant_instance_id,
            shared_claim_until=claim.claim_until.isoformat(),
            handler_kind="invalid-envelope/v0",
        )
        if (
            claim.claimant_instance_id != self.config.instance_id
            or claim.recipient_agent_id != self.config.agent_id
        ):
            self.state.mark_inbox_human_review(
                self.config.instance_id,
                claim.delivery_id,
                claim.thread_id,
                "ambiguous identity on malformed claimed envelope",
            )
            return {
                "claimed": True,
                "delivery_id": claim.delivery_id,
                "local_status": "AMBIGUOUS",
            }
        try:
            received = self.adapter.mark_received(
                claim.delivery_id, self.config.instance_id
            )
        except Exception:
            received = False
        if not received:
            self.state.mark_inbox_human_review(
                self.config.instance_id,
                claim.delivery_id,
                claim.thread_id,
                "ambiguous acknowledgement of malformed claimed envelope",
            )
            return {
                "claimed": True,
                "delivery_id": claim.delivery_id,
                "local_status": "AMBIGUOUS",
            }
        self.state.update_inbox_processing(
            claim.delivery_id,
            "RECEIVED",
            proposed_outcome=InboxOutcome.NOT_UNDERSTOOD.value,
        )
        try:
            completed = self.adapter.complete(
                claim.delivery_id,
                self.config.instance_id,
                InboxOutcome.NOT_UNDERSTOOD,
                {"handler": "invalid-envelope/v0", "reason": claim.reason[:512]},
            )
        except Exception:
            completed = False
        if not completed:
            self.state.mark_inbox_human_review(
                self.config.instance_id,
                claim.delivery_id,
                claim.thread_id,
                "ambiguous completion of malformed claimed envelope",
            )
            return {
                "claimed": True,
                "delivery_id": claim.delivery_id,
                "local_status": "AMBIGUOUS",
            }
        self.state.update_inbox_processing(
            claim.delivery_id, "HANDLED", last_error=claim.reason
        )
        return {
            "claimed": True,
            "delivery_id": claim.delivery_id,
            "local_status": "HANDLED",
            "outcome": InboxOutcome.NOT_UNDERSTOOD.value,
        }

    def _ambiguous(self, claim: InboxClaim, reason: str) -> dict[str, Any]:
        envelope = claim.envelope
        self.state.mark_inbox_human_review(
            self.config.instance_id,
            envelope.delivery_id,
            envelope.thread_id,
            reason,
            envelope.subject or "Shared inbox delivery",
        )
        return {
            "claimed": True,
            "delivery_id": envelope.delivery_id,
            "local_status": "AMBIGUOUS",
            "reason": reason,
        }
