from __future__ import annotations

import os
import signal
import stat
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch

from codex_supervisor.manager import (
    Manager,
    ManagerStore,
    ManagedRuntime,
    Provider,
    process_start_ticks,
    session_matches_controller,
    session_matches_provider,
)
from codex_supervisor.manager_cli import _follow_log, _projection, _render, main


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.command = self.root / "provider.py"
        self.command.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, time\n"
            'if sys.argv[1:] in (["login", "status"], ["login"]): raise SystemExit(0)\n'
            'if "exit-zero" in sys.argv[1:]: raise SystemExit(0)\n'
            'if "exit-now" in sys.argv[1:]: raise SystemExit(3)\n'
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        self.command.chmod(self.command.stat().st_mode | stat.S_IXUSR)
        self.store = ManagerStore(self.root / "state")
        self.manager = Manager(self.store)
        self.provider = Provider(
            "codex",
            str(self.command),
            ("login", "status"),
            ("login",),
            ("--model", "test-model"),
        )
        self.provider_patch = patch(
            "codex_supervisor.manager.PROVIDERS", {"codex": self.provider}
        )
        self.provider_patch.start()

    def tearDown(self) -> None:
        for session in list(self.store.all_active()):
            provider_pid = session.provider_pid
            self.manager.stop(session, force=True)
            if provider_pid:
                try:
                    os.waitpid(provider_pid, 0)
                except ChildProcessError:
                    pass
        self.provider_patch.stop()
        self.temp.cleanup()

    def test_start_records_controller_and_provider_identities(self) -> None:
        session = self.manager.start("codex", self.root)
        self.assertEqual(session.phase, "running")
        self.assertEqual(session.args, ["--model", "test-model"])
        self.assertTrue(session_matches_controller(session))
        self.assertTrue(session_matches_provider(session))
        self.assertEqual(session.controller_pid, os.getpid())
        self.assertEqual(
            session.provider_start_ticks, process_start_ticks(session.provider_pid)
        )
        self.assertEqual(self.store.active_for_workspace(self.root).id, session.id)

    def test_invalid_session_id_does_not_escape_state_directory(self) -> None:
        self.assertIsNone(self.store.load("../../outside"))

    def test_force_stop_clears_workspace_index(self) -> None:
        session = self.manager.start("codex", self.root)
        provider_pid = session.provider_pid
        self.assertIn("force stopped", self.manager.stop(session, force=True))
        os.waitpid(provider_pid, 0)
        self.assertIsNone(self.store.active_for_workspace(self.root))
        self.assertEqual(self.store.load(session.id).phase, "force-stopped")

    def test_runtime_heartbeats_then_stops_and_reaps_provider(self) -> None:
        session = self.manager.start("codex", self.root)
        runtime = ManagedRuntime(self.manager, heartbeat_interval=0.02, stop_grace=0.5)
        results: list[int] = []
        thread = threading.Thread(target=lambda: results.append(runtime.run(session)))
        thread.start()
        initial = session.heartbeat_at
        deadline = time.monotonic() + 1
        while session.heartbeat_at == initial and time.monotonic() < deadline:
            time.sleep(0.01)
        runtime.request_stop()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [130])
        stored = self.store.load(session.id)
        self.assertEqual(stored.phase, "stopped")
        self.assertFalse(session_matches_provider(stored))
        self.assertIsNone(self.store.active_for_workspace(self.root))

    def test_runtime_records_natural_nonzero_provider_exit(self) -> None:
        session = self.manager.start("codex", self.root, "exit-now")
        result = ManagedRuntime(self.manager, heartbeat_interval=0.02).run(session)
        self.assertEqual(result, 3)
        stored = self.store.load(session.id)
        self.assertEqual(stored.phase, "failed")
        self.assertIn("exit code 3", stored.reason)

    def test_runtime_records_natural_zero_provider_exit(self) -> None:
        session = self.manager.start("codex", self.root, "exit-zero")
        result = ManagedRuntime(self.manager, heartbeat_interval=0.02).run(session)
        self.assertEqual(result, 0)
        stored = self.store.load(session.id)
        self.assertEqual(stored.phase, "completed")
        self.assertIn("exit code 0", stored.reason)

    def test_external_stop_signals_controller_before_provider(self) -> None:
        session = self.manager.start("codex", self.root)
        session.controller_pid = 424242
        session.controller_start_ticks = "test"
        self.store.save(session)
        with (
            patch(
                "codex_supervisor.manager.session_matches_controller", return_value=True
            ),
            patch(
                "codex_supervisor.manager.session_matches_provider", return_value=True
            ),
            patch.object(self.manager, "terminate_provider") as terminate_provider,
            patch("codex_supervisor.manager.os.kill") as kill,
        ):
            self.manager.stop(session)
        kill.assert_called_once_with(424242, signal.SIGTERM)
        terminate_provider.assert_not_called()

    def test_status_projection_reports_verified_health(self) -> None:
        session = self.manager.start("codex", self.root)
        self.manager.heartbeat(session)
        projection = _projection(session)
        self.assertEqual(projection["health"], "healthy")
        self.assertTrue(projection["controller"]["identity_verified"])
        self.assertTrue(projection["provider_process"]["identity_verified"])

    def test_refresh_marks_stale_controller_as_attention(self) -> None:
        session = self.manager.start("codex", self.root)
        session.controller_start_ticks = "not-the-real-start-time"
        self.store.save(session)
        refreshed = self.manager.refresh(session)
        self.assertEqual(refreshed.phase, "attention")
        self.assertIn("controller process", refreshed.reason)

    def test_refresh_marks_stale_provider_as_attention(self) -> None:
        session = self.manager.start("codex", self.root)
        actual_start_ticks = session.provider_start_ticks
        session.provider_start_ticks = "not-the-real-start-time"
        self.store.save(session)
        refreshed = self.manager.refresh(session)
        self.assertEqual(refreshed.phase, "attention")
        self.assertIn("provider process", refreshed.reason)
        # Restore the verified identity so teardown can safely reap the real
        # child; production code correctly refuses the corrupted identity.
        session.provider_start_ticks = actual_start_ticks
        self.store.save(session)

    def test_text_and_json_status_share_the_same_projection(self) -> None:
        session = self.manager.start("codex", self.root)
        text_output = StringIO()
        json_output = StringIO()
        with redirect_stdout(text_output):
            _render([session], False)
        with redirect_stdout(json_output):
            _render([session], True)
        projected = json.loads(json_output.getvalue())[0]
        self.assertIn(projected["session_id"], text_output.getvalue())
        self.assertIn(projected["provider"], text_output.getvalue())
        self.assertIn(projected["health"], text_output.getvalue())

    def test_logs_follow_stops_when_controller_is_gone(self) -> None:
        session = self.manager.start("codex", self.root)
        output = StringIO()
        with (
            patch(
                "codex_supervisor.manager_cli.session_matches_controller",
                return_value=False,
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(_follow_log(self.store, session), 0)
        self.assertIn("started provider", output.getvalue())

    def test_launcher_only_session_state_is_loaded_for_visible_recovery(self) -> None:
        legacy_id = "legacy-session"
        self.store._write_json(
            self.store.session_path(legacy_id),
            {
                "id": legacy_id,
                "provider": "codex",
                "command": str(self.command),
                "workspace": str(self.root),
                "args": [],
                "pid": 12345,
                "process_start_ticks": "1",
                "phase": "running",
                "created_at": "2026-07-18T00:00:00+00:00",
            },
        )
        loaded = self.store.load(legacy_id)
        self.assertEqual(loaded.schema_version, 1)
        self.assertEqual(loaded.provider_pid, 12345)
        self.assertIsNone(loaded.controller_pid)

    def test_supervisor_scan_once_delegates_to_read_only_workflow(self) -> None:
        workflow_state = self.root / "workflow.sqlite3"
        snapshot = self.root / "threads.json"
        with patch(
            "codex_supervisor.manager_cli.unread_supervisor_main", return_value=0
        ) as delegated:
            self.assertEqual(
                main(
                    [
                        "scan-once",
                        "--workflow-state-path",
                        str(workflow_state),
                        "--inventory-snapshot",
                        str(snapshot),
                    ]
                ),
                0,
            )
        arguments = delegated.call_args.args[0]
        self.assertEqual(arguments[0], "scan-once")
        self.assertIn(str(workflow_state), arguments)
        self.assertIn(str(snapshot), arguments)


if __name__ == "__main__":
    unittest.main()
