from __future__ import annotations

import fcntl
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_supervisor.cli import main
from codex_supervisor.claude import ConservativeClaude, parse_decision
from codex_supervisor.codex import AppServerClient
from codex_supervisor.console import render_html
from codex_supervisor.health import heartbeat_check, write_heartbeat
from codex_supervisor.human_review_queue import render_markdown
from codex_supervisor.models import (
    DecisionKind,
    DeliveryReceipt,
    SupervisorConfig,
    TaskContext,
)
from codex_supervisor.providers import build_session_adapter
from codex_supervisor.scanner import CodexAppSnapshotInventory, UnreadScanner
from codex_supervisor.service import CONSOLE_SERVICE_NAME, SERVICE_NAME, render_units
from codex_supervisor.state import SupervisorState
from codex_supervisor.supervisor import Supervisor


class FakeInventory:
    def __init__(self, threads):
        self.threads = [dict(thread) for thread in threads]
        for index, thread in enumerate(self.threads):
            thread.setdefault("status", {"type": "idle"})
            if (
                thread["status"].get("type") == "idle"
                and thread.get("hasUnreadTurn") is True
            ):
                thread.setdefault("updatedAt", index + 1)
        self.scans = 0

    def list_unarchived_threads(self):
        self.scans += 1
        return self.threads


class FakeCodex(FakeInventory):
    def __init__(self, threads):
        super().__init__(threads)
        self.context_reads = []
        self.replies = []

    def read_context(self, candidate):
        self.context_reads.append(candidate.thread_id)
        return TaskContext(
            candidate, ("u1", "a1", "u2", "a2", "extra", "ignored"), "a2"
        )

    def send_reply(self, candidate, reply):
        self.replies.append((candidate.thread_id, reply))
        raise AssertionError("dry-run workflow must never call send_reply")


class FakeSession:
    def __init__(self, output, session_id="persistent-1"):
        self.output = output
        self.returned_id = session_id
        self.calls = []

    def decide(self, session_id, context, system_prompt):
        self.calls.append((session_id, context, system_prompt))
        return self.returned_id, self.output


class RecordingAppServer(AppServerClient):
    def __init__(self):
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        return {"thread": {"turns": []}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.state = SupervisorState(self.path)

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def make(
        self,
        threads,
        output='{"decision":"REPLY","reason":"clear","reply":"continue"}',
        session_id="persistent-1",
    ):
        codex = FakeCodex(threads)
        session = FakeSession(output, session_id)
        config = SupervisorConfig("host", self.path, Path("/tmp/codex.sock"))
        supervisor = Supervisor(
            config,
            UnreadScanner(codex, "host", config.supervisor_thread_id),
            codex,
            ConservativeClaude(session),
            self.state,
        )
        return supervisor, codex, session

    def test_only_idle_tasks_with_explicit_unread_signal_survive(self):
        result = UnreadScanner(
            FakeInventory(
                [
                    {"id": "old", "updatedAt": 9, "hasUnreadTurn": True},
                    {"id": "read", "updatedAt": 10, "hasUnreadTurn": False},
                    {"id": "new", "updatedAt": 11, "hasUnreadTurn": True},
                    {
                        "id": "active",
                        "hasUnreadTurn": True,
                        "status": {"type": "active", "activeFlags": []},
                    },
                ]
            ),
            "host",
        ).scan()
        self.assertTrue(result.unread_supported)
        self.assertEqual([item.thread_id for item in result.candidates], ["old", "new"])

    def test_missing_or_partial_unread_inventory_fails_closed(self):
        self.assertFalse(UnreadScanner(None, "host").scan().unread_supported)
        missing_status = FakeInventory([{"id": "x", "hasUnreadTurn": True}])
        del missing_status.threads[0]["status"]
        self.assertFalse(UnreadScanner(missing_status, "host").scan().unread_supported)
        self.assertFalse(
            UnreadScanner(FakeInventory([{"id": "x"}]), "host").scan().unread_supported
        )
        self.assertFalse(
            UnreadScanner(FakeInventory([{"id": "x", "hasUnreadTurn": "yes"}]), "host")
            .scan()
            .unread_supported
        )
        unstable = FakeInventory([{"id": "x", "hasUnreadTurn": True}])
        del unstable.threads[0]["updatedAt"]
        self.assertFalse(UnreadScanner(unstable, "host").scan().unread_supported)
        boolean_receipt = FakeInventory(
            [{"id": "x", "hasUnreadTurn": True, "updatedAt": True}]
        )
        self.assertFalse(UnreadScanner(boolean_receipt, "host").scan().unread_supported)
        missing_id = FakeInventory([{"hasUnreadTurn": True, "updatedAt": 1}])
        self.assertFalse(UnreadScanner(missing_id, "host").scan().unread_supported)

    def test_codex_app_snapshot_preserves_exact_host_unread_state(self):
        snapshot = Path(self.temp.name) / "threads.json"
        snapshot.write_text(
            '{"schemaVersion":2,"threads":['
            '{"id":"remote-unread","hostId":"remote","status":"idle","hasUnreadTurn":true,"updatedAt":20,"title":"One"},'
            '{"id":"remote-read","hostId":"remote","status":"idle","hasUnreadTurn":false,"updatedAt":21,"title":"Two"},'
            '{"id":"other-host","hostId":"local","status":"idle","hasUnreadTurn":true,"updatedAt":22,"title":"Three"}'
            "]}",
            encoding="utf-8",
        )
        result = UnreadScanner(
            CodexAppSnapshotInventory(snapshot, "remote"), "remote"
        ).scan()
        self.assertTrue(result.unread_supported)
        self.assertEqual(
            [item.thread_id for item in result.candidates], ["remote-unread"]
        )

    def test_invalid_codex_app_snapshot_fails_closed(self):
        snapshot = Path(self.temp.name) / "threads.json"
        snapshot.write_text('{"schemaVersion":1,"threads":[]}', encoding="utf-8")
        result = UnreadScanner(
            CodexAppSnapshotInventory(snapshot, "remote"), "remote"
        ).scan()
        self.assertFalse(result.unread_supported)
        self.assertIn("schemaVersion 2", result.note)

    def test_codex_app_snapshot_rejects_wrong_or_unavailable_host(self):
        snapshot = Path(self.temp.name) / "threads.json"
        snapshot.write_text(
            '{"schemaVersion":2,"threads":['
            '{"id":"one","hostId":"remote","status":"idle","hasUnreadTurn":true,"updatedAt":20}'
            '],"unavailableHosts":[]}',
            encoding="utf-8",
        )
        wrong = UnreadScanner(
            CodexAppSnapshotInventory(snapshot, "other"), "other"
        ).scan()
        self.assertFalse(wrong.unread_supported)
        snapshot.write_text(
            '{"schemaVersion":2,"threads":[],"unavailableHosts":["remote"]}',
            encoding="utf-8",
        )
        unavailable = UnreadScanner(
            CodexAppSnapshotInventory(snapshot, "remote"), "remote"
        ).scan()
        self.assertFalse(unavailable.unread_supported)

    def test_existing_terminal_marker_skips_context_and_decision_forever(self):
        self.state.mark_human_review("host", "a", "done")
        supervisor, codex, session = self.make([{"id": "a", "hasUnreadTurn": True}])
        supervisor.run_once()
        supervisor.run_once()
        self.assertEqual(codex.context_reads, [])
        self.assertEqual(session.calls, [])

    def test_empty_scan_never_invokes_decision_provider(self):
        supervisor, _, session = self.make([])
        self.assertEqual(supervisor.run_once()["candidates"], 0)
        self.assertEqual(session.calls, [])

    def test_one_decision_session_id_is_persisted_and_reused(self):
        supervisor, _, session = self.make(
            [
                {"id": "a", "hasUnreadTurn": True},
                {"id": "b", "hasUnreadTurn": True},
            ]
        )
        supervisor.run_once()
        self.assertEqual([call[0] for call in session.calls], [None, "persistent-1"])
        self.assertEqual(self.state.session_id(), "persistent-1")
        self.assertEqual(self.state.fable_turn_count("host", "a"), 1)
        self.assertEqual(self.state.fable_turn_count("host", "b"), 1)
        self.assertTrue(all(call[2] for call in session.calls))

    def test_supervisor_prompt_prefers_a_grounded_next_action_over_a_completion_claim(
        self,
    ):
        supervisor, _, session = self.make([{"id": "a", "hasUnreadTurn": True}])
        supervisor.run_once()
        prompt = session.calls[0][2]
        self.assertIn("Be usefully opinionated", prompt)
        self.assertIn("not an automatic stop", prompt)
        self.assertEqual(
            prompt,
            (
                Path(__file__).parents[1] / "src/codex_supervisor/supervisor_prompt.md"
            ).read_text(encoding="utf-8"),
        )

    def test_context_is_bounded(self):
        supervisor, _, session = self.make([{"id": "a", "hasUnreadTurn": True}])
        supervisor.run_once()
        self.assertEqual(session.calls[0][1].latest_visible_result, "a2")

    def test_decision_contract_only_allows_two_exact_shapes(self):
        self.assertEqual(
            parse_decision('{"decision":"REPLY","reason":"x","reply":"go"}').kind,
            DecisionKind.REPLY,
        )
        self.assertEqual(
            parse_decision('{"decision":"NOPE","reason":"x","reply":null}').kind,
            DecisionKind.HUMAN_REVIEW_NEEDED,
        )
        self.assertEqual(
            parse_decision(
                '{"decision":"REPLY","reason":"x","reply":"go","extra":1}'
            ).kind,
            DecisionKind.HUMAN_REVIEW_NEEDED,
        )

    def test_non_claude_provider_can_supply_a_schema_valid_decision(self):
        class StubAdapter:
            def decide(self, session_id, context, system_prompt):
                return (
                    "stub-session-1",
                    '{"decision":"REPLY","reason":"grounded","reply":"continue"}',
                )

        adapter = build_session_adapter(
            "stub", "unused", None, {"stub": lambda config: StubAdapter()}
        )
        candidate = (
            UnreadScanner(FakeInventory([{"id": "a", "hasUnreadTurn": True}]), "host")
            .scan()
            .candidates[0]
        )
        session_id, decision = ConservativeClaude(adapter).decide(
            None, TaskContext(candidate, (), None)
        )
        self.assertEqual(session_id, "stub-session-1")
        self.assertEqual(decision.kind, DecisionKind.REPLY)

    def test_claude_provider_preserves_current_command_and_model_defaults(self):
        with patch("codex_supervisor.providers.ClaudeCodeSession") as adapter_type:
            build_session_adapter("claude", "claude", None)
        adapter_type.assert_called_once_with(command="claude", model="fable")

    def test_reply_decision_is_recorded_but_never_delivered(self):
        supervisor, codex, _ = self.make([{"id": "a", "hasUnreadTurn": True}])
        outcome = supervisor.run_once()
        self.assertTrue(outcome["dry_run"])
        self.assertEqual(codex.replies, [])
        self.assertEqual(self.state.status()["dry_run_decision_count"], 1)
        self.assertEqual(self.state.status()["pending_delivery_count"], 0)

    def test_same_unread_result_can_be_analyzed_again_without_a_delivery_claim(self):
        supervisor, codex, _ = self.make([{"id": "a", "hasUnreadTurn": True}])
        supervisor.run_once()
        supervisor.run_once()
        self.assertEqual(codex.replies, [])
        self.assertEqual(self.state.status()["dry_run_decision_count"], 2)
        self.assertEqual(self.state.status()["pending_delivery_count"], 0)

    def test_review_recommendation_remains_a_dry_run(self):
        supervisor, codex, _ = self.make(
            [{"id": "a", "hasUnreadTurn": True}],
            '{"decision":"HUMAN_REVIEW_NEEDED","reason":"uncertain","reply":null}',
        )
        supervisor.run_once()
        self.assertFalse(self.state.is_human_review("host", "a"))
        self.assertEqual(codex.replies, [])
        self.assertEqual(self.state.status()["dry_run_decision_count"], 1)

    def test_context_and_classifier_errors_are_audited_without_terminal_markers(self):
        supervisor, codex, _ = self.make([{"id": "a", "hasUnreadTurn": True}])
        codex.read_context = lambda candidate: (_ for _ in ()).throw(
            TimeoutError("read timeout")
        )
        supervisor.run_once()
        self.assertFalse(self.state.is_human_review("host", "a"))
        self.assertEqual(self.state.status()["dry_run_decision_count"], 1)
        supervisor, _, session = self.make([{"id": "b", "hasUnreadTurn": True}])
        session.decide = lambda *args: (_ for _ in ()).throw(
            RuntimeError("classifier down")
        )
        supervisor.run_once()
        self.assertFalse(self.state.is_human_review("host", "b"))
        self.assertEqual(self.state.status()["dry_run_decision_count"], 2)

    def test_invalid_outputs_are_dry_run_review_recommendations(self):
        for index, output in enumerate(
            ("not json", '{"decision":"REPLY","reason":"","reply":"go"}')
        ):
            supervisor, codex, _ = self.make(
                [{"id": f"a{index}", "hasUnreadTurn": True}], output
            )
            supervisor.run_once()
            self.assertEqual(codex.replies, [])
            self.assertFalse(self.state.is_human_review("host", f"a{index}"))
        self.assertEqual(self.state.status()["dry_run_decision_count"], 2)

    def test_lock_rejects_overlap(self):
        supervisor, _, _ = self.make([])
        lock_path = self.path.with_suffix(".lock")
        lock_path.touch()
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(supervisor.run_once()["ran"])

    def test_excludes_own_thread(self):
        result = UnreadScanner(
            FakeInventory(
                [
                    {"id": "own", "hasUnreadTurn": True},
                    {"id": "other", "hasUnreadTurn": True},
                ]
            ),
            "host",
            "own",
        ).scan()
        self.assertEqual([item.thread_id for item in result.candidates], ["other"])

    def test_restart_preserves_marker_and_session(self):
        self.state.mark_human_review("host", "a", "x")
        self.state.set_session_id("p1")
        self.state.close()
        self.state = SupervisorState(self.path)
        self.assertTrue(self.state.is_human_review("host", "a"))
        self.assertEqual(self.state.session_id(), "p1")

    def test_human_review_queue_preserves_title_snapshot_and_uses_thread_ids(self):
        self.state.mark_human_review("host", "a", "needs approval", "Review this task")
        row = self.state.human_review_queue()[0]
        self.assertEqual(row["title"], "Review this task")
        self.assertIn("`a`", render_markdown([row]))

    def test_console_prioritizes_reason_and_hides_internal_task_ids(self):
        self.state.mark_human_review("host", "a", "needs human", "Task <one>")
        page = render_html(self.path, Path("/missing.sock"))
        self.assertIn("Fable human review queue", page)
        self.assertIn("Task &lt;one&gt;", page)
        self.assertIn("Why Fable stopped", page)
        self.assertNotIn("<code>a</code>", page)

    def test_console_keeps_filename_periods_inside_status_bullet(self):
        self.state.mark_human_review(
            "host",
            "a",
            "Choose from (.agents/, docs/) before proceeding. This needs human approval.",
            "Task",
        )
        page = render_html(self.path, Path("/missing.sock"))
        self.assertIn(
            "<strong>Status:</strong><ul><li>Choose from (.agents/, docs/) before proceeding.</li></ul>",
            page,
        )

    def test_console_limits_sections_to_four_short_sub_bullets(self):
        reason = (
            "Status is clear. "
            + " ".join(f"Evidence {index} is recorded." for index in range(6))
            + " To unblock, review the result."
        )
        self.state.mark_human_review("host", "a", reason, "Task")
        page = render_html(self.path, Path("/missing.sock"))
        self.assertNotIn("Evidence 5 is recorded.", page)
        self.assertNotIn("Evidence 6 is recorded.", page)

    def test_queue_command_reads_legacy_state_without_migrating_it(self):
        self.state.close()
        self.path.unlink()
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE human_review_tasks (host_id TEXT NOT NULL, thread_id TEXT NOT NULL, disposition TEXT NOT NULL, reason TEXT NOT NULL, marked_at TEXT NOT NULL, PRIMARY KEY (host_id, thread_id))"
        )
        db.execute(
            "INSERT INTO human_review_tasks VALUES ('host','legacy','HUMAN_REVIEW_NEEDED','reason','2026-07-13T00:00:00+00:00')"
        )
        db.commit()
        db.close()
        self.assertEqual(
            main(["human-review-queue", "--state-path", str(self.path)]), 0
        )
        db = sqlite3.connect(self.path)
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(human_review_tasks)")
        }
        db.close()
        self.assertNotIn("title", columns)

    def test_idle_delivery_transport_remains_dormant_for_later_live_work(self):
        client = RecordingAppServer()
        candidate = (
            UnreadScanner(
                FakeInventory([{"id": "idle", "hasUnreadTurn": True}]), "host"
            )
            .scan()
            .candidates[0]
        )
        receipt = client.send_reply(candidate, "continue")
        self.assertIsInstance(receipt, DeliveryReceipt)
        self.assertEqual(
            [call[0] for call in client.calls], ["thread/resume", "turn/start"]
        )

    def test_canary_evidence_store_remains_dormant_for_later_live_work(self):
        self.state.record_canary_evidence("canary-1", "inventory-v1", "delivery-v1")
        self.assertTrue(
            self.state.canary_matches("canary-1", "inventory-v1", "delivery-v1")
        )
        self.assertFalse(
            self.state.canary_matches("canary-1", "inventory-v2", "delivery-v1")
        )

    def test_delivery_claim_reconciliation_remains_fail_closed_for_later_live_work(
        self,
    ):
        self.assertTrue(self.state.claim_delivery("host", "a", 10))
        self.state.reconcile_delivery_claims({("host", "a", 10): "Task"}, 60)
        self.assertTrue(self.state.is_human_review("host", "a"))
        self.state.reset_human_review("host", "a")
        self.assertTrue(self.state.claim_delivery("host", "a", 11))
        self.state.await_clearance("host", "a", 11)
        self.state.reconcile_delivery_claims({}, 60)
        self.assertEqual(self.state.delivery_claim("host", "a", 11)[0], "CONFIRMED")

    def test_scan_once_is_wired_only_to_dry_run_builder(self):
        class StubSupervisor:
            def run_once(self):
                return {"ran": True, "dry_run": True, "candidates": 0}

        class StubState:
            def close(self):
                pass

        with patch(
            "codex_supervisor.cli.build_dry_run_supervisor",
            return_value=(StubSupervisor(), StubState()),
        ) as builder:
            self.assertEqual(main(["scan-once", "--state-path", str(self.path)]), 0)
        builder.assert_called_once()

    def test_scan_once_returns_failure_when_unread_capability_is_missing(self):
        class StubSupervisor:
            def run_once(self):
                return {
                    "ran": True,
                    "dry_run": True,
                    "unread_supported": False,
                    "candidates": 0,
                }

        class StubState:
            def close(self):
                pass

        with patch(
            "codex_supervisor.cli.build_dry_run_supervisor",
            return_value=(StubSupervisor(), StubState()),
        ):
            self.assertEqual(main(["scan-once", "--state-path", str(self.path)]), 1)

    def test_doctor_is_read_only_when_state_does_not_exist(self):
        missing = Path(self.temp.name) / "missing.sqlite3"
        self.assertEqual(
            main(
                [
                    "doctor",
                    "--state-path",
                    str(missing),
                    "--socket-path",
                    str(Path(self.temp.name) / "missing.sock"),
                ]
            ),
            0,
        )
        self.assertFalse(missing.exists())

    def test_heartbeat_reports_fresh_and_stale_progress(self):
        write_heartbeat(self.path, result="ok", interval=60, detail="ready")
        self.assertEqual(heartbeat_check(self.path, 1).status, "ok")
        heartbeat = self.path.with_suffix(".heartbeat.json")
        heartbeat.write_text(
            '{"finished_at":"2000-01-01T00:00:00+00:00","interval_seconds":1,"result":"ok"}',
            encoding="utf-8",
        )
        self.assertEqual(heartbeat_check(self.path, 0).status, "failed")

    def test_systemd_units_have_one_restart_authority_and_strict_watchdog(self):
        units = render_units(Path("/opt/bin/codex-unread-supervisor"), self.path, 45)
        worker = units[f"{SERVICE_NAME}.service"]
        self.assertIn("Restart=on-failure", worker)
        self.assertIn("StartLimitBurst=3", worker)
        console = units[f"{CONSOLE_SERVICE_NAME}.service"]
        self.assertIn("console --state-path", console)
        self.assertIn(
            "watchdog --state-path", units[f"{SERVICE_NAME}-watchdog.service"]
        )


if __name__ == "__main__":
    unittest.main()
