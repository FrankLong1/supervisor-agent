from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_supervisor.bootstrap import (
    DEFAULT_COCKPIT_PROMPT,
    ensure_codex_cockpit,
    read_binding,
)
from codex_supervisor.codex import AppServerError


THREAD_ID = "019f7771-5028-70e2-8169-560394910649"


class FakeClient:
    def __init__(self, threads: list[dict] | None = None):
        self.threads = threads or []
        self.requests: list[tuple[str, dict]] = []

    def list_unarchived_threads(self) -> list[dict]:
        return self.threads

    def request(self, method: str, params: dict) -> dict:
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": THREAD_ID}}
        return {}


class CockpitBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binding = self.root / "config" / "cockpit.env"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_creates_names_starts_and_persists_one_cockpit(self) -> None:
        client = FakeClient()
        result = ensure_codex_cockpit(
            client, workspace=self.root, binding_path=self.binding
        )
        self.assertTrue(result.created)
        self.assertEqual(read_binding(self.binding), THREAD_ID)
        self.assertEqual(self.binding.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            [method for method, _ in client.requests],
            ["thread/start", "thread/name/set", "turn/start"],
        )
        self.assertEqual(client.requests[0][1]["cwd"], str(self.root.resolve()))
        self.assertFalse(client.requests[0][1]["ephemeral"])
        self.assertEqual(client.requests[-1][1]["effort"], "xhigh")
        self.assertEqual(
            client.requests[-1][1]["input"][0]["text"], DEFAULT_COCKPIT_PROMPT
        )

    def test_adopts_one_existing_named_cockpit_without_waking_it(self) -> None:
        client = FakeClient(
            [{"id": THREAD_ID, "name": "SUPERVISOR AGENT", "status": {"type": "idle"}}]
        )
        result = ensure_codex_cockpit(
            client, workspace=self.root, binding_path=self.binding
        )
        self.assertFalse(result.created)
        self.assertEqual(read_binding(self.binding), THREAD_ID)
        self.assertEqual(client.requests, [])

    def test_recovers_partially_created_bound_task(self) -> None:
        self.binding.parent.mkdir(parents=True)
        self.binding.write_text(
            f"SUPERVISOR_COCKPIT_THREAD_ID={THREAD_ID}\n", encoding="utf-8"
        )
        client = FakeClient([{"id": THREAD_ID, "name": None}])
        result = ensure_codex_cockpit(
            client, workspace=self.root, binding_path=self.binding
        )
        self.assertTrue(result.created)
        self.assertEqual(
            [method for method, _ in client.requests],
            ["thread/name/set", "turn/start"],
        )

    def test_multiple_named_cockpits_fail_closed(self) -> None:
        client = FakeClient(
            [
                {"id": THREAD_ID, "name": "SUPERVISOR AGENT"},
                {
                    "id": "019f777d-b465-79b2-a956-ff82f7c2fe45",
                    "name": "SUPERVISOR AGENT",
                },
            ]
        )
        with self.assertRaisesRegex(AppServerError, "multiple unarchived"):
            ensure_codex_cockpit(client, workspace=self.root, binding_path=self.binding)
        self.assertFalse(self.binding.exists())

    def test_wrong_bound_title_fails_closed(self) -> None:
        self.binding.parent.mkdir(parents=True)
        self.binding.write_text(
            f"SUPERVISOR_COCKPIT_THREAD_ID={THREAD_ID}\n", encoding="utf-8"
        )
        client = FakeClient([{"id": THREAD_ID, "name": "Not the cockpit"}])
        with self.assertRaisesRegex(AppServerError, "title does not match"):
            ensure_codex_cockpit(client, workspace=self.root, binding_path=self.binding)

    def test_missing_bound_cockpit_fails_without_creating_a_duplicate(self) -> None:
        self.binding.parent.mkdir(parents=True)
        self.binding.write_text(
            f"SUPERVISOR_COCKPIT_THREAD_ID={THREAD_ID}\n", encoding="utf-8"
        )
        client = FakeClient()
        with self.assertRaisesRegex(AppServerError, "not an unarchived task"):
            ensure_codex_cockpit(client, workspace=self.root, binding_path=self.binding)
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
