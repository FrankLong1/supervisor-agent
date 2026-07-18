from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from codex_supervisor.worker import (
    WorkerLease,
    combined_tick,
    heartbeat_detail,
    run_worker,
)


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp.name) / "state.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_combined_tick_checks_both_sources_and_reports_canary_gate(self) -> None:
        calls: list[str] = []

        def local() -> dict[str, object]:
            calls.append("local")
            return {"reachable": True, "candidate_count": 0}

        def inbox() -> dict[str, object]:
            calls.append("inbox")
            return {
                "configured": True,
                "mode": "poll",
                "handling_enabled": False,
                "observed": 1,
            }

        result = combined_tick(local, inbox)
        self.assertEqual(calls, ["local", "inbox"])
        self.assertEqual(result["result"], "degraded")
        self.assertTrue(result["local"]["ok"])
        self.assertTrue(result["inbox"]["ok"])
        self.assertNotIn("password", heartbeat_detail(result))

    def test_source_failure_is_redacted_and_does_not_skip_other_source(self) -> None:
        called = False

        def failing() -> dict[str, object]:
            raise RuntimeError("postgresql://user:secret@example/db")

        def inbox() -> dict[str, object]:
            nonlocal called
            called = True
            return {"configured": False, "mode": "disabled"}

        result = combined_tick(failing, inbox)
        self.assertTrue(called)
        encoded = heartbeat_detail(result)
        self.assertNotIn("secret", encoded)
        self.assertEqual(result["local"]["error_type"], "RuntimeError")

    def test_worker_ticks_both_sources_repeatedly_and_writes_heartbeat(self) -> None:
        now = 0.0
        local_calls = 0
        inbox_calls = 0

        def clock() -> float:
            return now

        def wait(seconds: float) -> None:
            nonlocal now
            now += seconds

        def local() -> dict[str, object]:
            nonlocal local_calls
            local_calls += 1
            return {"reachable": True, "candidate_count": 0}

        def inbox() -> dict[str, object]:
            nonlocal inbox_calls
            inbox_calls += 1
            return {"configured": False, "mode": "disabled"}

        run_worker(
            state_path=self.state_path,
            interval=1,
            stop_requested=lambda: local_calls >= 2,
            local_poll=local,
            inbox_poll=inbox,
            clock=clock,
            wait=wait,
        )
        self.assertEqual((local_calls, inbox_calls), (2, 2))
        heartbeat = json.loads(
            self.state_path.with_suffix(".heartbeat.json").read_text(encoding="utf-8")
        )
        self.assertEqual(heartbeat["result"], "ok")
        self.assertIn('"source":"cloud_sql_inbox"', heartbeat["detail"])

    def test_only_one_worker_lease_can_be_active(self) -> None:
        with WorkerLease(self.state_path):
            with self.assertRaisesRegex(RuntimeError, "already active"):
                with WorkerLease(self.state_path):
                    self.fail("second worker lease unexpectedly acquired")


if __name__ == "__main__":
    unittest.main()
