from __future__ import annotations

import tempfile
import threading
import unittest
import sqlite3
from pathlib import Path

from codex_supervisor.cli import main
from codex_supervisor.claude import ConservativeClaude, parse_decision
from codex_supervisor.console import render_html
from codex_supervisor.codex import AppServerClient
from codex_supervisor.health import heartbeat_check, write_heartbeat
from codex_supervisor.human_review_queue import render_markdown
from codex_supervisor.models import DecisionKind, DeliveryReceipt, SupervisorConfig, TaskContext
from codex_supervisor.scanner import UnreadScanner
from codex_supervisor.service import SERVICE_NAME, render_units
from codex_supervisor.state import SupervisorState
from codex_supervisor.supervisor import Supervisor


class FakeInventory:
    def __init__(self, threads):
        self.threads = [dict(thread) for thread in threads]
        for index, thread in enumerate(self.threads):
            thread.setdefault("status", {"type": "idle"})
            if thread["status"].get("type") == "idle":
                thread.setdefault("updatedAt", index + 1)
        self.scans = 0
    def list_unarchived_threads(self): self.scans += 1; return self.threads


class FakeCodex(FakeInventory):
    def __init__(self, threads, fail_reply=False): super().__init__(threads); self.context_reads = []; self.replies = []; self.fail_reply = fail_reply
    def read_context(self, candidate):
        self.context_reads.append(candidate.thread_id)
        return TaskContext(candidate, ("u1", "a1", "u2", "a2", "extra", "ignored"), "a2")
    def send_reply(self, candidate, reply):
        if self.fail_reply: raise TimeoutError("delivery unknown")
        self.replies.append((candidate.thread_id, reply))
        return DeliveryReceipt(True, f"turn-{candidate.thread_id}")


class FakeSession:
    def __init__(self, output, session_id="persistent-1"): self.output, self.returned_id, self.calls = output, session_id, []
    def decide(self, session_id, context, system_prompt): self.calls.append((session_id, context, system_prompt)); return self.returned_id, self.output


class RecordingAppServer(AppServerClient):
    def __init__(self): self.calls = []
    def request(self, method, params): self.calls.append((method, params)); return {"thread": {"turns": []}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / "state.sqlite3"; self.state = SupervisorState(self.path)
    def tearDown(self): self.state.close(); self.temp.cleanup()
    def make(self, threads, output='{"decision":"REPLY","reason":"clear","reply":"continue"}', **overrides):
        codex = FakeCodex(threads, overrides.pop("fail_reply", False)); session = FakeSession(output, overrides.pop("session_id", "persistent-1"))
        enabled = overrides.pop("enabled", False)
        if enabled:
            overrides.update(shadow_mode=False, allow_replies=True, canary_evidence_id="canary-1", inventory_identity="inventory-v1", delivery_identity="delivery-v1")
            self.state.record_canary_evidence("canary-1", "inventory-v1", "delivery-v1")
        config = SupervisorConfig("host", self.path, Path("/tmp/codex.sock"), **overrides)
        return Supervisor(config, UnreadScanner(codex, "host", config.supervisor_thread_id), codex, ConservativeClaude(session), self.state), codex, session

    def test_1_only_idle_tasks_survive(self):
        result = UnreadScanner(FakeInventory([{"id":"old","updatedAt":9},{"id":"new","updatedAt":10},{"id":"active","status":{"type":"active","activeFlags":[]}},{"id":"approval","status":{"type":"active","activeFlags":["waitingOnApproval"]}}]), "host").scan()
        self.assertEqual([x.thread_id for x in result.candidates], ["old", "new"])
    def test_2_missing_or_partial_status_inventory_fails_closed(self):
        self.assertFalse(UnreadScanner(None, "host").scan().unread_supported)
        partial = FakeInventory([{"id":"x","hasUnreadTurn":True},{"id":"y"}])
        del partial.threads[1]["status"]
        self.assertFalse(UnreadScanner(partial, "host").scan().unread_supported)
        inventory = FakeInventory([{"id":"x"}])
        del inventory.threads[0]["updatedAt"]
        self.assertFalse(UnreadScanner(inventory, "host").scan().unread_supported)
    def test_3_marker_skips_context_and_claude_forever(self):
        self.state.mark_human_review("host", "a", "done"); sup, codex, session = self.make([{"id":"a","hasUnreadTurn":True}]); sup.run_once(); sup.run_once()
        self.assertEqual(codex.context_reads, []); self.assertEqual(session.calls, [])
    def test_4_empty_scan_never_invokes_claude(self):
        sup, _, session = self.make([]); self.assertEqual(sup.run_once()["candidates"], 0); self.assertEqual(session.calls, [])
    def test_5_one_session_id_is_persisted_and_reused(self):
        sup, _, session = self.make([{"id":"a","hasUnreadTurn":True},{"id":"b","hasUnreadTurn":True}]); sup.run_once()
        self.assertEqual([x[0] for x in session.calls], [None, "persistent-1"]); self.assertEqual(self.state.session_id(), "persistent-1")
        self.assertTrue(all(call[2] for call in session.calls))

    def test_supervisor_prompt_prefers_a_grounded_next_action_over_a_completion_claim(self):
        sup, _, session = self.make([{"id":"a","hasUnreadTurn":True}]); sup.run_once()
        prompt = session.calls[0][2]
        self.assertIn("Be usefully opinionated", prompt)
        self.assertIn("not an automatic stop", prompt)
        self.assertEqual(prompt, (Path(__file__).parents[1] / "src/codex_supervisor/supervisor_prompt.md").read_text(encoding="utf-8"))
    def test_6_context_is_bounded(self):
        sup, _, session = self.make([{"id":"a","hasUnreadTurn":True}]); sup.run_once(); self.assertEqual(session.calls[0][1].latest_visible_result, "a2")
    def test_7_contract_only_allows_two_exact_shapes(self):
        self.assertEqual(parse_decision('{"decision":"REPLY","reason":"x","reply":"go"}').kind, DecisionKind.REPLY)
        self.assertEqual(parse_decision('{"decision":"NOPE","reason":"x","reply":null}').kind, DecisionKind.HUMAN_REVIEW_NEEDED)
        self.assertEqual(parse_decision('{"decision":"REPLY","reason":"x","reply":"go","extra":1}').kind, DecisionKind.HUMAN_REVIEW_NEEDED)
    def test_8_invalid_or_uncertain_outputs_become_terminal(self):
        for output in ("not json", '{"decision":"HUMAN_REVIEW_NEEDED","reason":"uncertain","reply":null}', '{"decision":"REPLY","reason":"","reply":"go"}'):
            self.state.reset_human_review("host", "a")
            sup, _, _ = self.make([{"id":"a","hasUnreadTurn":True}], output, shadow_mode=False)
            sup.run_once()
            self.assertTrue(self.state.is_human_review("host", "a"))
    def test_9_reply_delivered_once_when_enabled(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], enabled=True); sup.run_once(); self.assertEqual(codex.replies, [("a", "continue")])
    def test_10_unchanged_result_can_be_selected_only_if_inventory_still_marks_unread(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], enabled=True); sup.run_once(); sup.run_once(); self.assertEqual(len(codex.replies), 1)
        codex.threads[0]["status"] = {"type": "idle"}; sup.run_once(); self.assertEqual(len(codex.replies), 1)
    def test_11_human_review_never_replies(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], '{"decision":"HUMAN_REVIEW_NEEDED","reason":"complete","reply":null}', shadow_mode=False); sup.run_once(); self.assertTrue(self.state.is_human_review("host", "a")); self.assertEqual(codex.replies, [])
    def test_12_ambiguous_delivery_marks_and_never_retries(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], enabled=True, fail_reply=True); sup.run_once(); sup.run_once(); self.assertTrue(self.state.is_human_review("host", "a")); self.assertEqual(codex.replies, [])
    def test_13_lock_rejects_overlap(self):
        sup, _, _ = self.make([]); lock_path = self.path.with_suffix(".lock"); lock_path.touch()
        import fcntl
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); self.assertFalse(sup.run_once()["ran"])
    def test_14_excludes_own_thread(self):
        result = UnreadScanner(FakeInventory([{"id":"own","hasUnreadTurn":True},{"id":"other","hasUnreadTurn":True}]), "host", "own").scan(); self.assertEqual([x.thread_id for x in result.candidates], ["other"])
    def test_15_restart_preserves_marker_and_session(self):
        self.state.mark_human_review("host", "a", "x"); self.state.set_session_id("p1"); self.state.close(); self.state = SupervisorState(self.path)
        self.assertTrue(self.state.is_human_review("host", "a")); self.assertEqual(self.state.session_id(), "p1")

    def test_human_review_queue_preserves_title_snapshot_and_uses_thread_links(self):
        sup, _, _ = self.make([{"id":"a", "name":"Review this task", "hasUnreadTurn":True}], '{"decision":"HUMAN_REVIEW_NEEDED","reason":"needs approval","reply":null}', shadow_mode=False)
        sup.run_once()
        row = self.state.human_review_queue()[0]
        self.assertEqual(row["title"], "Review this task")
        self.assertIn("`a`", render_markdown([row]))

    def test_console_renders_read_only_queue_with_thread_link(self):
        self.state.mark_human_review("host", "a", "needs human", "Task <one>")
        page = render_html(self.path, Path("/missing.sock"))
        self.assertIn("Fable human review queue", page)
        self.assertIn("Task &lt;one&gt;", page)
        self.assertIn("<code>a</code>", page)
        self.assertIn("<code>a</code>", page)

    def test_existing_human_review_rows_receive_blank_title_and_render_gracefully(self):
        self.state.mark_human_review("host", "legacy", "older marker")
        row = self.state.human_review_queue()[0]
        self.assertEqual(row["title"], "")
        self.assertIn("Untitled task", render_markdown([row]))

    def test_queue_command_reads_legacy_state_without_migrating_it(self):
        self.state.close()
        self.path.unlink()
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE human_review_tasks (host_id TEXT NOT NULL, thread_id TEXT NOT NULL, disposition TEXT NOT NULL, reason TEXT NOT NULL, marked_at TEXT NOT NULL, PRIMARY KEY (host_id, thread_id))")
        db.execute("INSERT INTO human_review_tasks VALUES ('host','legacy','HUMAN_REVIEW_NEEDED','reason with | delimiter','2026-07-13T00:00:00+00:00')")
        db.commit(); db.close()
        self.assertEqual(main(["human-review-queue", "--state-path", str(self.path)]), 0)
        db = sqlite3.connect(self.path)
        columns = {row[1] for row in db.execute("PRAGMA table_info(human_review_tasks)")}
        db.close()
        self.assertNotIn("title", columns)

    def test_idle_delivery_uses_resume_then_start_and_active_delivery_uses_steer(self):
        client = RecordingAppServer()
        candidate = UnreadScanner(FakeInventory([{"id":"idle","hasUnreadTurn":True}]), "host").scan().candidates[0]
        client.send_reply(candidate, "continue")
        self.assertEqual([call[0] for call in client.calls], ["thread/resume", "turn/start"])
        self.assertNotIn("resume", client.calls[1][1])
        client.calls.clear()
        active = UnreadScanner(FakeInventory([{"id":"active","hasUnreadTurn":True,"activeTurnId":"turn-1"}]), "host").scan().candidates[0]
        client.send_reply(active, "continue")
        self.assertEqual([call[0] for call in client.calls], ["turn/steer"])

    def test_context_and_classifier_errors_become_terminal_markers(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}])
        codex.read_context = lambda candidate: (_ for _ in ()).throw(TimeoutError("read timeout"))
        sup.run_once()
        self.assertTrue(self.state.is_human_review("host", "a"))
        self.state.reset_human_review("host", "a")
        sup, _, session = self.make([{"id":"a","hasUnreadTurn":True}])
        session.decide = lambda *args: (_ for _ in ()).throw(RuntimeError("classifier down"))
        sup.run_once()
        self.assertTrue(self.state.is_human_review("host", "a"))

    def test_explicit_live_mode_delivers_without_canary_gate(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], shadow_mode=False, allow_replies=True, canary_evidence_id="missing", inventory_identity="inventory-v1", delivery_identity="delivery-v1")
        sup.run_once()
        self.assertEqual(codex.replies, [("a", "continue")])
        self.assertFalse(self.state.is_human_review("host", "a"))

    def test_pending_delivery_timeout_becomes_terminal_marker(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], enabled=True, delivery_confirmation_timeout_seconds=0)
        sup.run_once(); sup.run_once()
        self.assertEqual(len(codex.replies), 1)
        self.assertTrue(self.state.is_human_review("host", "a"))

    def test_interrupted_delivery_claim_and_new_receipt_are_handled_safely(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True, "updatedAt":10}], enabled=True)
        self.assertTrue(self.state.claim_delivery("host", "a", 10))
        sup.run_once()
        self.assertTrue(self.state.is_human_review("host", "a"))
        self.assertEqual(codex.replies, [])
        self.state.reset_human_review("host", "a")
        codex.threads[0]["updatedAt"] = 11
        sup.run_once()
        self.assertEqual(codex.replies, [("a", "continue")])

    def test_doctor_is_read_only_when_state_does_not_exist(self):
        missing = Path(self.temp.name) / "missing.sqlite3"
        self.assertEqual(main(["doctor", "--state-path", str(missing), "--socket-path", str(Path(self.temp.name) / "missing.sock")]), 0)
        self.assertFalse(missing.exists())

    def test_heartbeat_reports_fresh_and_stale_progress(self):
        write_heartbeat(self.path, result="ok", interval=60, detail="ready")
        self.assertEqual(heartbeat_check(self.path, 1).status, "ok")
        heartbeat = self.path.with_suffix(".heartbeat.json")
        heartbeat.write_text('{"finished_at":"2000-01-01T00:00:00+00:00","interval_seconds":1,"result":"ok"}', encoding="utf-8")
        self.assertEqual(heartbeat_check(self.path, 0).status, "failed")

    def test_systemd_units_have_one_restart_authority_and_strict_watchdog(self):
        units = render_units(Path("/opt/bin/codex-unread-supervisor"), self.path, 45)
        worker = units[f"{SERVICE_NAME}.service"]
        self.assertIn("Restart=on-failure", worker)
        self.assertIn("RestartSec=30", worker)
        self.assertIn("StartLimitBurst=3", worker)
        self.assertNotIn("--strict", worker)
        self.assertIn("watchdog --state-path", units[f"{SERVICE_NAME}-watchdog.service"])
        self.assertIn("--strict", units[f"{SERVICE_NAME}-watchdog.service"])


if __name__ == "__main__": unittest.main()
