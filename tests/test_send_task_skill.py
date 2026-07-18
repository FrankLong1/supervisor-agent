from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents/skills/send-shared-inbox-task/scripts/send_task.py"
)
SPEC = importlib.util.spec_from_file_location("send_shared_inbox_task", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SendTaskSkillTests(unittest.TestCase):
    def test_directory_resolves_only_the_other_workstation(self) -> None:
        self.assertEqual(MODULE.resolve("research@alice", "frank"), "helper@bob")
        self.assertEqual(MODULE.resolve("helper@bob", "alice@"), "research@alice")
        with self.assertRaisesRegex(ValueError, "sender itself"):
            MODULE.resolve("research@alice", "alice")
        with self.assertRaisesRegex(ValueError, "must be"):
            MODULE.resolve("unknown@example.com", "alice")

    def test_stable_key_is_content_bound(self) -> None:
        first = MODULE.stable_key("research@alice", "helper@bob", "Subject", "Body")
        self.assertEqual(
            first,
            MODULE.stable_key("research@alice", "helper@bob", "Subject", "Body"),
        )
        self.assertNotEqual(
            first,
            MODULE.stable_key("research@alice", "helper@bob", "Subject", "Other"),
        )
        self.assertLessEqual(len(first), 200)

    def test_script_invokes_supervisor_without_a_shell_and_with_exact_address(
        self,
    ) -> None:
        completed = subprocess.CompletedProcess([], 0)
        output = StringIO()
        with (
            patch.dict(
                os.environ,
                {"SUPERVISOR_INBOX_AGENT_ADDRESS": "helper@bob"},
                clear=True,
            ),
            patch.object(MODULE.subprocess, "run", return_value=completed) as run,
            redirect_stdout(output),
        ):
            code = MODULE.main(
                [
                    "--to",
                    "alice",
                    "--subject",
                    "Investigate fixture",
                    "--body-text",
                    "Inspect and report.",
                    "--supervisor-bin",
                    "/opt/bin/supervisor",
                ]
            )
        self.assertEqual(code, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/opt/bin/supervisor", "inbox", "send-task"])
        self.assertEqual(
            command[command.index("--recipient-address") + 1], "research@alice"
        )
        self.assertIn("--body-file", command)
        self.assertNotIn("Inspect and report.", command)
        self.assertEqual(run.call_args.kwargs, {"check": False})

    def test_dry_run_redacts_body_and_self_send_never_invokes_supervisor(self) -> None:
        output = StringIO()
        with (
            patch.dict(
                os.environ,
                {"SUPERVISOR_INBOX_AGENT_ADDRESS": "research@alice"},
                clear=True,
            ),
            patch.object(MODULE.subprocess, "run") as run,
            redirect_stdout(output),
        ):
            code = MODULE.main(
                [
                    "--to",
                    "frank@",
                    "--subject",
                    "Bounded task",
                    "--body-text",
                    "Private task body",
                    "--dry-run",
                ]
            )
        self.assertEqual(code, 0)
        projection = json.loads(output.getvalue())
        self.assertEqual(projection["recipient_address"], "helper@bob")
        self.assertNotIn("Private task body", output.getvalue())
        run.assert_not_called()

        error = StringIO()
        with (
            patch.dict(
                os.environ,
                {"SUPERVISOR_INBOX_AGENT_ADDRESS": "research@alice"},
                clear=True,
            ),
            patch.object(MODULE.subprocess, "run") as run,
            redirect_stderr(error),
        ):
            code = MODULE.main(
                [
                    "--to",
                    "alice",
                    "--subject",
                    "Self task",
                    "--body-text",
                    "Must fail",
                ]
            )
        self.assertEqual(code, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
