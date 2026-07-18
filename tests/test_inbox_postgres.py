from __future__ import annotations

import os
import unittest
from uuid import uuid4

from codex_supervisor.inbox.models import InboxOutcome, MessageKind
from codex_supervisor.inbox.postgres import PostgresInboxAdapter


DSN = os.environ.get("SUPERVISOR_INBOX_TEST_DSN")


@unittest.skipUnless(DSN, "SUPERVISOR_INBOX_TEST_DSN is not configured")
class PostgresInboxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import psycopg
        from psycopg.rows import dict_row

        cls.psycopg = psycopg
        cls.dict_row = dict_row
        cls.admin = psycopg.connect(DSN, autocommit=True)
        with cls.admin.cursor() as cursor:
            cursor.execute(
                "SELECT agent_id,address FROM agent_inbox.agents WHERE address IN ('research@alice','helper@bob')"
            )
            cls.agents = {
                address: str(agent_id) for agent_id, address in cursor.fetchall()
            }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.admin.close()

    @classmethod
    def adapter(cls, role: str) -> PostgresInboxAdapter:
        def connect(dsn):
            connection = cls.psycopg.connect(dsn, row_factory=cls.dict_row)
            with connection.cursor() as cursor:
                cursor.execute(f'SET SESSION AUTHORIZATION "{role}"')
            connection.commit()
            return connection

        return PostgresInboxAdapter(DSN, connect=connect)

    def test_stored_function_lifecycle_is_tenant_scoped_and_idempotent(self) -> None:
        sender = self.adapter("alice_runtime")
        recipient_a = self.adapter("bob_runtime")
        recipient_b = self.adapter("bob_runtime")
        key = f"supervisor-adapter-it-{uuid4()}"
        try:
            sent = sender.send_message(
                sender_agent_id=self.agents["research@alice"],
                recipient_address="helper@bob",
                kind=MessageKind.QUESTION,
                subject="adapter integration",
                body_text="bounded fixture",
                body_json={"integration": True},
                idempotency_key=key,
            )
            before = recipient_a.list_deliveries(100, "QUEUED")
            self.assertIn(sent.delivery_id, {item.delivery_id for item in before})
            after = recipient_a.list_deliveries(100, "QUEUED")
            self.assertEqual(
                [item.delivery_id for item in before],
                [item.delivery_id for item in after],
                "listing must not mutate shared delivery state",
            )
            claim = recipient_a.claim_next("adapter-a", 60, self.agents["helper@bob"])
            self.assertIsNotNone(claim)
            self.assertEqual(claim.envelope.delivery_id, sent.delivery_id)
            self.assertIsNone(
                recipient_b.claim_next("adapter-b", 60, self.agents["helper@bob"])
            )
            self.assertFalse(
                recipient_b.mark_received(sent.delivery_id, "wrong-instance")
            )
            self.assertTrue(recipient_a.mark_received(sent.delivery_id, "adapter-a"))
            self.assertFalse(
                recipient_b.complete(
                    sent.delivery_id, "wrong-instance", InboxOutcome.COMPLETED, {}
                )
            )
            self.assertTrue(
                recipient_a.complete(
                    sent.delivery_id,
                    "adapter-a",
                    InboxOutcome.COMPLETED,
                    {"integration": True},
                )
            )
            retried = sender.send_message(
                sender_agent_id=self.agents["research@alice"],
                recipient_address="helper@bob",
                kind=MessageKind.QUESTION,
                subject="adapter integration",
                body_text="bounded fixture",
                body_json={"integration": True},
                idempotency_key=key,
            )
            self.assertFalse(retried.created)
            self.assertEqual(retried.message_id, sent.message_id)
            self.assertEqual(retried.delivery_id, sent.delivery_id)
        finally:
            sender.close()
            recipient_a.close()
            recipient_b.close()

    def test_runtime_role_cannot_issue_raw_mailbox_dml(self) -> None:
        connection = self.psycopg.connect(DSN)
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET SESSION AUTHORIZATION "bob_runtime"')
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    cursor.execute(
                        "UPDATE agent_inbox.deliveries SET status='COMPLETED'"
                    )
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
