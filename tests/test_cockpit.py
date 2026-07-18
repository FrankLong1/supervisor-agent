from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_supervisor.cockpit import (
    CockpitBridge,
    CockpitConfig,
    cockpit_prompt,
    safe_status_snapshot,
)
from codex_supervisor.codex import AppServerClient, AppServerError
from codex_supervisor.models import CockpitDeliveryReceipt
from codex_supervisor.state import SupervisorState


THREAD_ID = "019f7771-5028-70e2-8169-560394910649"


def tick() -> dict:
    return {
        "result": "degraded",
        "local": {
            "source": "local_app_server",
            "ok": True,
            "reachable": True,
            "unread_supported": False,
            "candidate_count": 0,
            "note": "controlled note",
        },
        "inbox": {
            "source": "cloud_sql_inbox",
            "ok": True,
            "configured": True,
            "mode": "poll",
            "handling_enabled": False,
            "observed": 1,
            "deliveries": [
                {
                    "subject": "SECRET SUBJECT",
                    "body_text": "SECRET BODY",
                    "delivery_id": "SECRET DELIVERY",
                }
            ],
            "routes": {"human_review": 1},
        },
    }


class FakeCockpitClient:
    def __init__(self):
        self.calls: list[dict] = []
        self.fail = False

    def send_cockpit_update(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise AppServerError("secret server payload")
        return CockpitDeliveryReceipt("turn-1", "turn/start")


class CockpitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.state = SupervisorState(self.path)

    def tearDown(self) -> None:
        self.state.close()
        self.temp.cleanup()

    def test_configuration_is_explicit_and_uuid_bound(self) -> None:
        self.assertIsNone(CockpitConfig.from_env({}).thread_id)
        self.assertEqual(
            CockpitConfig.from_env(
                {"SUPERVISOR_COCKPIT_THREAD_ID": THREAD_ID}
            ).thread_id,
            THREAD_ID,
        )
        with self.assertRaisesRegex(ValueError, "must be a UUID"):
            CockpitConfig.from_env({"SUPERVISOR_COCKPIT_THREAD_ID": "not-a-task"})

    def test_snapshot_and_prompt_exclude_untrusted_content_and_ids(self) -> None:
        snapshot = safe_status_snapshot(tick(), self.state)
        prompt = cockpit_prompt(snapshot)
        self.assertEqual(snapshot["inbox"]["queued_count"], 1)
        self.assertEqual(snapshot["inbox"]["routes"], {"human_review": 1})
        self.assertIn(
            "authoritative Codex hasUnreadTurn signal is unavailable",
            snapshot["mutation_blockers"],
        )
        self.assertIn(
            "generic local Codex task mutation is disabled by supervisor policy",
            snapshot["mutation_blockers"],
        )
        self.assertIn("not a human-authored message", prompt)
        for secret in ("SECRET SUBJECT", "SECRET BODY", "SECRET DELIVERY"):
            self.assertNotIn(secret, prompt)

    def test_bridge_delivers_once_per_status_edge_and_audits_transport(self) -> None:
        client = FakeCockpitClient()
        bridge = CockpitBridge(CockpitConfig(THREAD_ID), client, self.state)
        first = bridge.publish(tick())
        second = bridge.publish(tick())
        self.assertTrue(first["delivered"])
        self.assertEqual(second["reason"], "unchanged")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["expected_title"], "SUPERVISOR AGENT")
        self.assertEqual(client.calls[0]["reasoning_effort"], "xhigh")
        self.assertEqual(
            self.state.cockpit_status()["last_transport"], "turn/start"
        )
        self.assertEqual(self.state.cockpit_status()["pending_count"], 0)

    def test_failed_delivery_records_only_error_type_and_retries_pending(self) -> None:
        client = FakeCockpitClient()
        client.fail = True
        bridge = CockpitBridge(CockpitConfig(THREAD_ID), client, self.state)
        with self.assertRaises(AppServerError):
            bridge.publish(tick())
        status = self.state.cockpit_status()
        self.assertEqual(status["pending_count"], 1)
        self.assertEqual(status["last_error_type"], "AppServerError")
        client.fail = False
        self.assertTrue(bridge.publish(tick())["delivered"])

    def test_active_cockpit_defers_and_keeps_latest_edge_pending(self) -> None:
        class DeferredClient(FakeCockpitClient):
            def send_cockpit_update(self, **kwargs):
                self.calls.append(kwargs)
                return CockpitDeliveryReceipt(
                    None, "deferred", delivered=False, reason="cockpit_active"
                )

        client = DeferredClient()
        bridge = CockpitBridge(CockpitConfig(THREAD_ID), client, self.state)
        result = bridge.publish(tick())
        self.assertFalse(result["delivered"])
        self.assertEqual(result["reason"], "cockpit_active")
        self.assertEqual(self.state.cockpit_status()["pending_count"], 1)


class StubAppServerClient(AppServerClient):
    def __init__(self, threads: list[dict], turns: list[dict] | None = None):
        self.threads = threads
        self.turns = turns or []
        self.requests: list[tuple[str, dict]] = []

    def list_unarchived_threads(self) -> list[dict]:
        return self.threads

    def request(self, method: str, params: dict) -> dict:
        self.requests.append((method, params))
        if method == "thread/read":
            return {"thread": {"turns": self.turns}}
        if method == "turn/steer":
            return {"turnId": "active-turn"}
        if method == "turn/start":
            return {"turn": {"id": "new-turn"}}
        return {"thread": {"id": THREAD_ID}}


class AppServerCockpitTests(unittest.TestCase):
    def test_active_cockpit_defers_without_mutating_the_turn(self) -> None:
        client = StubAppServerClient(
            [{"id": THREAD_ID, "name": "SUPERVISOR AGENT", "status": {"type": "active"}}],
            [{"id": "active-turn", "status": "inProgress"}],
        )
        receipt = client.send_cockpit_update(
            thread_id=THREAD_ID,
            expected_title="SUPERVISOR AGENT",
            message="automated",
            client_message_id="message-1",
            reasoning_effort="xhigh",
        )
        self.assertFalse(receipt.delivered)
        self.assertEqual(receipt.reason, "cockpit_active")
        self.assertEqual(client.requests, [])

    def test_idle_cockpit_resumes_then_starts_a_turn(self) -> None:
        client = StubAppServerClient(
            [{"id": THREAD_ID, "name": "SUPERVISOR AGENT", "status": {"type": "idle"}}]
        )
        receipt = client.send_cockpit_update(
            thread_id=THREAD_ID,
            expected_title="SUPERVISOR AGENT",
            message="automated",
            client_message_id="message-1",
            reasoning_effort="xhigh",
        )
        self.assertEqual(receipt.delivery_id, "new-turn")
        self.assertEqual(
            [method for method, _ in client.requests], ["thread/resume", "turn/start"]
        )
        self.assertEqual(client.requests[-1][1]["effort"], "xhigh")

    def test_title_mismatch_fails_before_any_mutation(self) -> None:
        client = StubAppServerClient(
            [{"id": THREAD_ID, "name": "Another task", "status": {"type": "idle"}}]
        )
        with self.assertRaisesRegex(AppServerError, "title does not match"):
            client.send_cockpit_update(
                thread_id=THREAD_ID,
                expected_title="SUPERVISOR AGENT",
                message="automated",
                client_message_id="message-1",
                reasoning_effort="xhigh",
            )
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
