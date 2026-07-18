from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import tempfile
import unittest
from pathlib import Path

from codex_supervisor.inbox.config import InboxConfig, InboxMode
from codex_supervisor.inbox.fake import FakeInboxAdapter
from codex_supervisor.inbox.models import (
    ClaimedEnvelopeValidationError,
    InboxClaim,
    InboxEnvelope,
    InboxOutcome,
    InboxValidationError,
    InvalidInboxClaim,
    MessageKind,
)
from codex_supervisor.inbox.postgres import PostgresInboxAdapter
from codex_supervisor.inbox.routing import InboxRoute, route_envelope
from codex_supervisor.inbox.service import (
    CANARY_EVIDENCE_ID,
    HANDLER_IDENTITY,
    InboxService,
)
from codex_supervisor.state import SupervisorState


IDS = {
    "message": "00000000-0000-0000-0000-000000000001",
    "delivery": "00000000-0000-0000-0000-000000000002",
    "thread": "00000000-0000-0000-0000-000000000003",
    "principal": "00000000-0000-0000-0000-000000000004",
    "sender": "00000000-0000-0000-0000-000000000005",
    "recipient": "00000000-0000-0000-0000-000000000006",
}


def envelope(
    kind: MessageKind = MessageKind.QUESTION,
    *,
    delivery_id: str | None = None,
    unattended: bool = False,
    canary: bool = False,
) -> InboxEnvelope:
    return InboxEnvelope(
        IDS["message"],
        delivery_id or IDS["delivery"],
        IDS["thread"],
        None,
        IDS["principal"],
        IDS["sender"],
        "sender@alice",
        "Alice agent",
        IDS["recipient"],
        kind,
        "Bounded subject",
        "untrusted body",
        {"supervisor_canary": True} if canary else {},
        datetime.now(UTC),
        None,
        unattended,
    )


def claim(item: InboxEnvelope) -> InboxClaim:
    now = datetime.now(UTC)
    return InboxClaim(item, "stable-instance", now, now + timedelta(seconds=120))


class InboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.state = SupervisorState(self.path)
        self.config = InboxConfig(
            mode=InboxMode.ONE_SHOT,
            dsn="postgresql://user:secret@localhost/agent_inbox?token=hidden",
            instance_id="stable-instance",
            agent_id=IDS["recipient"],
            agent_address="helper@bob",
        )

    def tearDown(self) -> None:
        self.state.close()
        self.temp.cleanup()

    def test_configuration_defaults_disabled_and_masks_credentials(self) -> None:
        defaults = InboxConfig.from_env({})
        self.assertEqual(defaults.mode, InboxMode.DISABLED)
        self.assertEqual(defaults.poll_seconds, 30)
        self.assertEqual(
            self.config.masked_target, "postgresql://localhost/agent_inbox"
        )
        with self.assertRaises(ValueError):
            InboxConfig.from_env({"SUPERVISOR_INBOX_MODE": "one-shot"})

    def test_strict_row_parser_rejects_unknown_kinds_and_oversized_payloads(
        self,
    ) -> None:
        row = {
            "message_id": IDS["message"],
            "delivery_id": IDS["delivery"],
            "thread_id": IDS["thread"],
            "reply_to_message_id": None,
            "sender_principal_id": IDS["principal"],
            "sender_agent_id": IDS["sender"],
            "sender_address": "sender@alice",
            "sender_display_name": "Alice",
            "recipient_agent_id": IDS["recipient"],
            "kind": "FUTURE_KIND",
            "subject": "",
            "body_text": "",
            "body_json": {},
            "created_at": datetime.now(UTC),
            "expires_at": None,
        }
        with self.assertRaisesRegex(InboxValidationError, "unsupported message kind"):
            InboxEnvelope.from_row(row)
        row["kind"] = "NOTE"
        row["body_text"] = "x" * (64 * 1024 + 1)
        with self.assertRaisesRegex(InboxValidationError, "body_text exceeds"):
            InboxEnvelope.from_row(row)

    def test_deterministic_routes_cover_every_contract_kind(self) -> None:
        expected = {
            MessageKind.NOTE: InboxRoute.REFERENCE,
            MessageKind.QUESTION: InboxRoute.HUMAN_REVIEW,
            MessageKind.TASK_PROPOSAL: InboxRoute.HUMAN_REVIEW,
            MessageKind.TASK_ACCEPTED: InboxRoute.REFERENCE,
            MessageKind.TASK_DECLINED: InboxRoute.REFERENCE,
            MessageKind.PROGRESS: InboxRoute.CORRELATE,
            MessageKind.RESULT: InboxRoute.SURFACE_RESULT,
            MessageKind.NEEDS_HUMAN: InboxRoute.HUMAN_REVIEW,
            MessageKind.NOT_UNDERSTOOD: InboxRoute.NOT_UNDERSTOOD,
        }
        self.assertEqual(
            {kind: route_envelope(envelope(kind)).route for kind in MessageKind},
            expected,
        )
        self.assertEqual(
            route_envelope(envelope(MessageKind.QUESTION, unattended=True)).route,
            InboxRoute.DETERMINISTIC_REPLY,
        )
        self.assertEqual(
            route_envelope(
                envelope(MessageKind.TASK_PROPOSAL, unattended=True)
            ).outcome,
            InboxOutcome.NEEDS_HUMAN,
        )

    def test_scan_once_is_shared_read_only_and_stores_no_bodies(self) -> None:
        item = envelope(MessageKind.RESULT)
        adapter = FakeInboxAdapter((item,))
        service = InboxService(self.config, adapter, self.state)
        result = service.scan_once()
        self.assertTrue(result["read_only"])
        self.assertEqual(adapter.claims, [])
        self.assertEqual(adapter.received, [])
        self.assertEqual(adapter.sends, [])
        self.assertEqual(adapter.completions, [])
        columns = {
            row[1]
            for row in self.state.db.execute(
                "PRAGMA table_info(supervisor_inbox_observations)"
            )
        }
        self.assertNotIn("body_text", columns)
        self.assertNotIn("body_json", columns)

    def test_poll_observes_continuously_but_claims_only_after_canary(self) -> None:
        item = envelope(MessageKind.NOTE)
        adapter = FakeInboxAdapter((item,))
        adapter.claim = claim(item)
        config = replace(self.config, mode=InboxMode.POLL)
        service = InboxService(config, adapter, self.state)

        gated = service.poll_once()
        self.assertEqual(gated["observed"], 1)
        self.assertFalse(gated["handling_enabled"])
        self.assertEqual(adapter.claims, [])
        service.poll_once()
        self.assertEqual(self.state.inbox_status()["observation_count"], 1)

        self.state.record_inbox_canary(
            evidence_id=CANARY_EVIDENCE_ID,
            contract_version=adapter.contract_version,
            adapter_identity=adapter.adapter_identity,
            principal_identity=adapter.identity,
            instance_id=config.instance_id,
            handler_identity=HANDLER_IDENTITY,
            delivery_id="canary",
            reply_message_id="reply",
        )
        handled = service.poll_once()
        self.assertTrue(handled["handling_enabled"])
        self.assertEqual(handled["local_status"], "HANDLED")
        self.assertEqual(len(adapter.claims), 1)

    def test_local_claim_is_committed_before_shared_receipt(self) -> None:
        item = envelope(MessageKind.NOTE)

        class OrderingFake(FakeInboxAdapter):
            def mark_received(inner, delivery_id, instance_id):
                self.assertEqual(
                    self.state.inbox_processing(delivery_id)["local_status"], "CLAIMED"
                )
                return super().mark_received(delivery_id, instance_id)

        adapter = OrderingFake((item,))
        adapter.claim = claim(item)
        self.state.record_inbox_canary(
            evidence_id=CANARY_EVIDENCE_ID,
            contract_version=adapter.contract_version,
            adapter_identity=adapter.adapter_identity,
            principal_identity=adapter.identity,
            instance_id=self.config.instance_id,
            handler_identity=HANDLER_IDENTITY,
            delivery_id="canary-delivery",
            reply_message_id="canary-reply",
        )
        result = InboxService(self.config, adapter, self.state).run_once()
        self.assertEqual(result["local_status"], "HANDLED")
        self.assertEqual(adapter.sends, [])

    def test_canary_requires_explicit_marker_and_sender_observation(self) -> None:
        item = envelope(canary=True)
        adapter = FakeInboxAdapter((item,))
        adapter.claim = claim(item)
        service = InboxService(self.config, adapter, self.state)
        result = service.canary(item.delivery_id)
        self.assertEqual(result["local_status"], "REPLIED")
        self.assertFalse(result["canary_enrolled"])
        reply_id = result["reply_message_id"]
        with self.assertRaises(ValueError):
            service.canary(item.delivery_id, IDS["message"])
        verified = service.canary(item.delivery_id, reply_id)
        self.assertTrue(verified["canary_enrolled"])
        rechecked = service.canary(item.delivery_id)
        self.assertTrue(rechecked["canary_enrolled"])
        self.assertFalse(rechecked["sender_verification_required"])
        self.assertEqual(len(adapter.sends), 1)
        self.assertTrue(
            self.state.inbox_canary_matches(
                evidence_id=CANARY_EVIDENCE_ID,
                contract_version=adapter.contract_version,
                adapter_identity=adapter.adapter_identity,
                principal_identity=adapter.identity,
                instance_id=self.config.instance_id,
                handler_identity=HANDLER_IDENTITY,
            )
        )

    def test_ambiguous_send_is_terminal_and_key_survives_restart(self) -> None:
        item = envelope(unattended=True)
        adapter = FakeInboxAdapter((item,))
        adapter.claim = claim(item)
        adapter.fail_at = "send"
        self.state.record_inbox_canary(
            evidence_id=CANARY_EVIDENCE_ID,
            contract_version=adapter.contract_version,
            adapter_identity=adapter.adapter_identity,
            principal_identity=adapter.identity,
            instance_id=self.config.instance_id,
            handler_identity=HANDLER_IDENTITY,
            delivery_id="canary",
            reply_message_id="reply",
        )
        result = InboxService(self.config, adapter, self.state).run_once()
        self.assertEqual(result["local_status"], "AMBIGUOUS")
        key = self.state.inbox_processing(item.delivery_id)["reply_idempotency_key"]
        self.assertTrue(key.startswith("supervisor-inbox-v0:"))
        self.state.close()
        self.state = SupervisorState(self.path)
        self.assertEqual(
            self.state.inbox_processing(item.delivery_id)["reply_idempotency_key"], key
        )
        self.assertEqual(self.state.inbox_status()["ambiguous_count"], 1)
        self.assertEqual(self.state.inbox_status()["inbox_human_review_count"], 1)

    def test_postgres_adapter_uses_only_fixed_functions_and_integer_lease(self) -> None:
        item = envelope(canary=True)
        now = datetime.now(UTC)
        envelope_row = {
            "delivery_id": item.delivery_id,
            "message_id": item.message_id,
            "thread_id": item.thread_id,
            "reply_to_message_id": None,
            "sender_principal_id": item.sender_principal_id,
            "sender_agent_id": item.sender_agent_id,
            "sender_address": item.sender_address,
            "sender_display_name": item.sender_display_name,
            "recipient_agent_id": item.recipient_agent_id,
            "recipient_address": "recipient@bob",
            "kind": item.kind.value,
            "subject": item.subject,
            "body_text": item.body_text,
            "body_json": dict(item.body_json),
            "created_at": now,
            "expires_at": None,
            "status": "CLAIMED",
            "available_at": now,
            "claimed_by": "stable-instance",
            "claimed_at": now,
            "claim_until": now + timedelta(seconds=120),
            "received_at": None,
            "completed_at": None,
            "outcome": None,
            "allow_unattended_execution": False,
        }

        class Cursor:
            description = [("result",)]

            def __init__(inner, connection):
                inner.connection = connection
                inner.rows = []

            def __enter__(inner):
                return inner

            def __exit__(inner, *args):
                return None

            def execute(inner, sql, params):
                inner.connection.calls.append((sql, params))
                if "session_user" in sql:
                    inner.rows = [{"authenticated_identity": "runtime_bob"}]
                elif (
                    "inbox_list_deliveries" in sql or "inbox_claim_next_delivery" in sql
                ):
                    inner.rows = [envelope_row]
                elif "inbox_send_message" in sql:
                    inner.rows = [
                        {
                            "message_id": "00000000-0000-0000-0000-000000000901",
                            "delivery_id": "00000000-0000-0000-0000-000000000902",
                            "resolved_thread_id": item.thread_id,
                            "created": True,
                        }
                    ]
                else:
                    inner.rows = [{"result": True}]

            def fetchall(inner):
                return inner.rows

        class Connection:
            def __init__(inner):
                inner.calls = []

            def cursor(inner):
                return Cursor(inner)

            def commit(inner):
                pass

            def rollback(inner):
                pass

            def close(inner):
                pass

        connection = Connection()
        adapter = PostgresInboxAdapter("unused", connect=lambda dsn: connection)
        self.assertEqual(adapter.authenticated_identity(), "runtime_bob")
        self.assertEqual(len(adapter.list_deliveries(2, "QUEUED")), 1)
        self.assertEqual(
            adapter.claim_next(
                "stable-instance", 120, IDS["recipient"]
            ).claimant_instance_id,
            "stable-instance",
        )
        self.assertTrue(adapter.mark_received(item.delivery_id, "stable-instance"))
        self.assertTrue(
            adapter.complete(
                item.delivery_id,
                "stable-instance",
                InboxOutcome.COMPLETED,
                {"safe": True},
            )
        )
        adapter.send_message(
            sender_agent_id=IDS["recipient"],
            recipient_address="sender@alice",
            kind=MessageKind.RESULT,
            subject="receipt",
            body_text="bounded",
            body_json={},
            idempotency_key="stable",
            thread_id=item.thread_id,
            reply_to_message_id=item.message_id,
        )
        claim_call = next(
            call for call in connection.calls if "inbox_claim_next_delivery" in call[0]
        )
        self.assertIs(type(claim_call[1][1]), int)
        sql = "\n".join(call[0].lower() for call in connection.calls)
        self.assertNotIn(" insert ", f" {sql} ")
        self.assertNotIn(" update ", f" {sql} ")
        self.assertNotIn(" delete ", f" {sql} ")
        allowed = (
            "inbox_list_deliveries",
            "inbox_claim_next_delivery",
            "inbox_mark_received",
            "inbox_complete_delivery",
            "inbox_send_message",
            "session_user",
        )
        self.assertTrue(
            all(
                any(name in statement.lower() for name in allowed)
                for statement, _ in connection.calls
            )
        )

    def test_malformed_live_claim_is_completed_without_executing_content(self) -> None:
        now = datetime.now(UTC)

        class MalformedClaimFake(FakeInboxAdapter):
            def claim_next(inner, instance_id, lease_seconds, recipient_agent_id=None):
                raise ClaimedEnvelopeValidationError(
                    InvalidInboxClaim(
                        delivery_id=IDS["delivery"],
                        message_id=IDS["message"],
                        thread_id=IDS["thread"],
                        recipient_agent_id=IDS["recipient"],
                        claimant_instance_id="stable-instance",
                        claim_until=now + timedelta(seconds=60),
                        reason="claimed envelope validation failed: unsupported message kind",
                    )
                )

        adapter = MalformedClaimFake()
        self.state.record_inbox_canary(
            evidence_id=CANARY_EVIDENCE_ID,
            contract_version=adapter.contract_version,
            adapter_identity=adapter.adapter_identity,
            principal_identity=adapter.identity,
            instance_id=self.config.instance_id,
            handler_identity=HANDLER_IDENTITY,
            delivery_id="canary",
            reply_message_id="reply",
        )
        result = InboxService(self.config, adapter, self.state).run_once()
        self.assertEqual(result["outcome"], InboxOutcome.NOT_UNDERSTOOD.value)
        self.assertEqual(adapter.sends, [])
        self.assertEqual(adapter.received, [(IDS["delivery"], "stable-instance")])
        self.assertEqual(adapter.completions[0][2], InboxOutcome.NOT_UNDERSTOOD)
        self.assertNotIn("body", str(self.state.inbox_processing(IDS["delivery"])))

    def test_send_task_queues_only_a_task_proposal_with_attributed_sender(self) -> None:
        adapter = FakeInboxAdapter()
        service = InboxService(self.config, adapter, self.state)
        result = service.send_task(
            recipient_address="research@alice",
            subject="Review the bounded fixture",
            body_text="Please inspect the fixture and report the result.",
            idempotency_key="skill-task:stable-key",
        )
        self.assertTrue(result["queued"])
        self.assertFalse(result["accepted_by_recipient"])
        self.assertEqual(adapter.sends[0]["kind"], MessageKind.TASK_PROPOSAL)
        self.assertEqual(adapter.sends[0]["sender_agent_id"], IDS["recipient"])
        self.assertEqual(adapter.sends[0]["recipient_address"], "research@alice")
        self.assertEqual(adapter.sends[0]["idempotency_key"], "skill-task:stable-key")

    def test_send_task_rejects_self_addressing_and_missing_sender_address(self) -> None:
        adapter = FakeInboxAdapter()
        service = InboxService(self.config, adapter, self.state)
        with self.assertRaisesRegex(ValueError, "configured sender"):
            service.send_task(
                recipient_address="helper@bob",
                subject="No self-send",
                body_text="This must not be queued.",
                idempotency_key="self-send",
            )
        config = InboxConfig(
            mode=InboxMode.ONE_SHOT,
            dsn="unused",
            instance_id="stable-instance",
            agent_id=IDS["recipient"],
        )
        with self.assertRaisesRegex(ValueError, "AGENT_ADDRESS"):
            InboxService(config, adapter, self.state).send_task(
                recipient_address="research@alice",
                subject="Missing attribution",
                body_text="This must not be queued.",
                idempotency_key="missing-address",
            )
        self.assertEqual(adapter.sends, [])

    def test_send_canary_queues_only_the_fixed_synthetic_question(self) -> None:
        adapter = FakeInboxAdapter()
        result = InboxService(self.config, adapter, self.state).send_canary(
            recipient_address="research@alice",
            idempotency_key="operator-canary:stable-key",
        )
        self.assertTrue(result["queued"])
        self.assertEqual(result["kind"], MessageKind.QUESTION.value)
        self.assertEqual(adapter.sends[0]["kind"], MessageKind.QUESTION)
        self.assertEqual(
            adapter.sends[0]["body_json"], {"supervisor_canary": True}
        )
        self.assertNotIn("task", adapter.sends[0]["body_text"].casefold())


if __name__ == "__main__":
    unittest.main()
