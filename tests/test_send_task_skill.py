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

ALICE_ID = "7a3fa6fa-2f49-42c9-bb6a-d4a9eafed720"
FRANK_ID = "fa212e75-7581-457b-a918-4ac8bc617bbc"
ALICE = "alice@gravitationalventures.com"
FRANK = "frank@gravitationalventures.com"


class SendTaskSkillTests(unittest.TestCase):
    def test_directory_resolves_only_the_other_workstation(self) -> None:
        sender, recipient = MODULE.resolve(ALICE, ALICE_ID, FRANK)
        self.assertEqual(sender, ALICE)
        self.assertEqual(recipient["principal"], FRANK)
        sender, recipient = MODULE.resolve(FRANK, FRANK_ID, ALICE)
        self.assertEqual(sender, FRANK)
        self.assertEqual(recipient["principal"], ALICE)
        with self.assertRaisesRegex(ValueError, "sender itself"):
            MODULE.resolve(ALICE, ALICE_ID, ALICE)
        with self.assertRaisesRegex(ValueError, "do not identify"):
            MODULE.resolve(ALICE, FRANK_ID, FRANK)

    def test_directory_contains_only_fixed_real_user_addresses(self) -> None:
        self.assertEqual(set(MODULE.DIRECTORY), {ALICE, FRANK})
        self.assertNotIn("alice", MODULE.DIRECTORY)
        self.assertNotIn("frank@", MODULE.DIRECTORY)

    def test_stable_key_is_content_bound(self) -> None:
        first = MODULE.stable_key(ALICE, FRANK, "Subject", "Body")
        self.assertEqual(
            first,
            MODULE.stable_key(ALICE, FRANK, "Subject", "Body"),
        )
        self.assertNotEqual(
            first,
            MODULE.stable_key(ALICE, FRANK, "Subject", "Other"),
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
                {
                    "SUPERVISOR_INBOX_AGENT_ADDRESS": FRANK,
                    "SUPERVISOR_INBOX_AGENT_ID": FRANK_ID,
                },
                clear=True,
            ),
            patch.object(MODULE.subprocess, "run", return_value=completed) as run,
            redirect_stdout(output),
        ):
            code = MODULE.main(
                [
                    "--to",
                    ALICE,
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
            command[command.index("--recipient-address") + 1], ALICE
        )
        self.assertIn("--body-file", command)
        self.assertNotIn("Inspect and report.", command)
        self.assertEqual(run.call_args.kwargs, {"check": False})

    def test_dry_run_redacts_body_and_self_send_never_invokes_supervisor(self) -> None:
        output = StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "SUPERVISOR_INBOX_AGENT_ADDRESS": ALICE,
                    "SUPERVISOR_INBOX_AGENT_ID": ALICE_ID,
                },
                clear=True,
            ),
            patch.object(MODULE.subprocess, "run") as run,
            redirect_stdout(output),
        ):
            code = MODULE.main(
                [
                    "--to",
                    FRANK,
                    "--subject",
                    "Bounded task",
                    "--body-text",
                    "Private task body",
                    "--dry-run",
                ]
            )
        self.assertEqual(code, 0)
        projection = json.loads(output.getvalue())
        self.assertEqual(projection["recipient_address"], FRANK)
        self.assertEqual(
            projection["sender_principal"], "alice@gravitationalventures.com"
        )
        self.assertEqual(
            projection["recipient_principal"], "frank@gravitationalventures.com"
        )
        self.assertNotIn("Private task body", output.getvalue())
        run.assert_not_called()

        error = StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "SUPERVISOR_INBOX_AGENT_ADDRESS": ALICE,
                    "SUPERVISOR_INBOX_AGENT_ID": ALICE_ID,
                },
                clear=True,
            ),
            patch.object(MODULE.subprocess, "run") as run,
            redirect_stderr(error),
        ):
            code = MODULE.main(
                [
                    "--to",
                    ALICE,
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
