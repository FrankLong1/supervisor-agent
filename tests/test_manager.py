from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_supervisor.manager import Manager, ManagerStore, Provider, process_start_ticks, session_matches_process


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.command = self.root / "provider.py"
        self.command.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, time\n"
            "if sys.argv[1:] in ([\"login\", \"status\"], [\"login\"]): raise SystemExit(0)\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        self.command.chmod(self.command.stat().st_mode | stat.S_IXUSR)
        self.store = ManagerStore(self.root / "state")
        self.manager = Manager(self.store)
        self.provider = Provider("codex", str(self.command), ("login", "status"), ("login",))

    def tearDown(self) -> None:
        for session in self.store.all_active():
            self.manager.stop(session, force=True)
        self.temp.cleanup()

    def test_start_records_an_identified_process_and_stop_targets_it(self) -> None:
        with patch("codex_supervisor.manager.PROVIDERS", {"codex": self.provider}):
            session = self.manager.start("codex", self.root)
        self.assertEqual(session.phase, "running")
        self.assertTrue(session_matches_process(session))
        self.assertEqual(session.process_start_ticks, process_start_ticks(session.pid))
        self.assertEqual(self.store.active_for_workspace(self.root).id, session.id)
        self.assertIn("stop requested", self.manager.stop(session))

    def test_invalid_session_id_does_not_escape_state_directory(self) -> None:
        self.assertIsNone(self.store.load("../../outside"))

    def test_stale_workspace_index_is_cleared(self) -> None:
        with patch("codex_supervisor.manager.PROVIDERS", {"codex": self.provider}):
            session = self.manager.start("codex", self.root)
        self.manager.stop(session, force=True)
        os.waitpid(session.pid, 0)
        self.assertIsNone(self.store.active_for_workspace(self.root))


if __name__ == "__main__":
    unittest.main()
