from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from codex_supervisor.claude import ConservativeClaude, parse_decision
from codex_supervisor.codex import AppServerClient
from codex_supervisor.models import DecisionKind, SupervisorConfig, TaskContext
from codex_supervisor.scanner import UnreadScanner
from codex_supervisor.state import SupervisorState
from codex_supervisor.supervisor import Supervisor


class FakeInventory:
    def __init__(self, threads): self.threads = threads; self.scans = 0
    def list_unarchived_threads(self): self.scans += 1; return self.threads


class FakeCodex(FakeInventory):
    def __init__(self, threads, fail_reply=False): super().__init__(threads); self.context_reads = []; self.replies = []; self.fail_reply = fail_reply
    def read_context(self, candidate):
        self.context_reads.append(candidate.thread_id)
        return TaskContext(candidate, ("u1", "a1", "u2", "a2", "extra", "ignored"), "a2")
    def send_reply(self, candidate, reply):
        if self.fail_reply: raise TimeoutError("delivery unknown")
        self.replies.append((candidate.thread_id, reply))


class FakeSession:
    def __init__(self, output, session_id="persistent-1"): self.output, self.returned_id, self.calls = output, session_id, []
    def decide(self, session_id, context): self.calls.append((session_id, context)); return self.returned_id, self.output


class RecordingAppServer(AppServerClient):
    def __init__(self): self.calls = []
    def request(self, method, params): self.calls.append((method, params)); return {"thread": {"turns": []}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / "state.sqlite3"; self.state = SupervisorState(self.path)
    def tearDown(self): self.state.close(); self.temp.cleanup()
    def make(self, threads, output='{"decision":"REPLY","reason":"clear","reply":"continue"}', **overrides):
        codex = FakeCodex(threads, overrides.pop("fail_reply", False)); session = FakeSession(output, overrides.pop("session_id", "persistent-1"))
        config = SupervisorConfig("host", self.path, Path("/tmp/codex.sock"), **overrides)
        return Supervisor(config, UnreadScanner(codex, "host", config.supervisor_thread_id), codex, ConservativeClaude(session), self.state), codex, session

    def test_1_only_unarchived_unread_tasks_survive(self):
        result = UnreadScanner(FakeInventory([{"id":"old","hasUnreadTurn":True,"unreadAt":9},{"id":"new","hasUnreadTurn":True,"unreadAt":10},{"id":"read","hasUnreadTurn":False}]), "host").scan()
        self.assertEqual([x.thread_id for x in result.candidates], ["old", "new"])
    def test_2_missing_or_partial_verified_inventory_fails_closed(self):
        self.assertFalse(UnreadScanner(None, "host").scan().unread_supported)
        self.assertFalse(UnreadScanner(FakeInventory([{"id":"x","hasUnreadTurn":True},{"id":"y"}]), "host").scan().unread_supported)
    def test_3_marker_skips_context_and_claude_forever(self):
        self.state.mark_human_review("host", "a", "done"); sup, codex, session = self.make([{"id":"a","hasUnreadTurn":True}]); sup.run_once(); sup.run_once()
        self.assertEqual(codex.context_reads, []); self.assertEqual(session.calls, [])
    def test_4_empty_scan_never_invokes_claude(self):
        sup, _, session = self.make([]); self.assertEqual(sup.run_once()["candidates"], 0); self.assertEqual(session.calls, [])
    def test_5_one_session_id_is_persisted_and_reused(self):
        sup, _, session = self.make([{"id":"a","hasUnreadTurn":True},{"id":"b","hasUnreadTurn":True}]); sup.run_once()
        self.assertEqual([x[0] for x in session.calls], [None, "persistent-1"]); self.assertEqual(self.state.session_id(), "persistent-1")
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
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], shadow_mode=False, allow_replies=True, canary_verified=True); sup.run_once(); self.assertEqual(codex.replies, [("a", "continue")])
    def test_10_unchanged_result_can_be_selected_only_if_inventory_still_marks_unread(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], shadow_mode=False, allow_replies=True, canary_verified=True); sup.run_once(); codex.threads[0]["hasUnreadTurn"] = False; sup.run_once(); self.assertEqual(len(codex.replies), 1)
    def test_11_human_review_never_replies(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], '{"decision":"HUMAN_REVIEW_NEEDED","reason":"complete","reply":null}', shadow_mode=False); sup.run_once(); self.assertTrue(self.state.is_human_review("host", "a")); self.assertEqual(codex.replies, [])
    def test_12_ambiguous_delivery_marks_and_never_retries(self):
        sup, codex, _ = self.make([{"id":"a","hasUnreadTurn":True}], shadow_mode=False, allow_replies=True, canary_verified=True, fail_reply=True); sup.run_once(); sup.run_once(); self.assertTrue(self.state.is_human_review("host", "a")); self.assertEqual(codex.replies, [])
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


if __name__ == "__main__": unittest.main()
