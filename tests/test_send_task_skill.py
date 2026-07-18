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
DIRECTORY_ENV = {
    "SUPERVISOR_INBOX_ALICE_AGENT_ADDRESS": "agent@alice",
    "SUPERVISOR_INBOX_ALICE_AGENT_ID": ALICE_ID,
    "SUPERVISOR_INBOX_FRANK_AGENT_ADDRESS": "agent@frank",
    "SUPERVISOR_INBOX_FRANK_AGENT_ID": FRANK_ID,
}


class SendTaskSkillTests(unittest.TestCase):
    def test_directory_resolves_only_the_other_workstation(self) -> None:
        directory = MODULE.configured_directory(DIRECTORY_ENV)
        sender, recipient = MODULE.resolve(
            "agent@alice", ALICE_ID, "frank", directory
        )
        self.assertEqual(sender, "alice")
        self.assertEqual(recipient["address"], "agent@frank")
        sender, recipient = MODULE.resolve(
            "agent@frank", FRANK_ID, "alice@gravitationalventures.com", directory
        )
        self.assertEqual(sender, "frank")
        self.assertEqual(recipient["address"], "agent@alice")
        with self.assertRaisesRegex(ValueError, "sender itself"):
            MODULE.resolve("agent@alice", ALICE_ID, "alice", directory)
        with self.assertRaisesRegex(ValueError, "do not identify"):
            MODULE.resolve("agent@alice", FRANK_ID, "frank", directory)

    def test_directory_requires_complete_distinct_deployment_outputs(self) -> None:
        missing_frank_address = dict(DIRECTORY_ENV)
        del missing_frank_address["SUPERVISOR_INBOX_FRANK_AGENT_ADDRESS"]
        with self.assertRaisesRegex(ValueError, "FRANK_AGENT_ADDRESS"):
            MODULE.configured_directory(missing_frank_address)
        duplicated = {
            **DIRECTORY_ENV,
            "SUPERVISOR_INBOX_FRANK_AGENT_ADDRESS": "agent@alice",
        }
        with self.assertRaisesRegex(ValueError, "addresses must be distinct"):
            MODULE.configured_directory(duplicated)

    def test_stable_key_is_content_bound(self) -> None:
        first = MODULE.stable_key("agent@alice", "agent@frank", "Subject", "Body")
        self.assertEqual(
            first,
            MODULE.stable_key("agent@alice", "agent@frank", "Subject", "Body"),
        )
        self.assertNotEqual(
            first,
            MODULE.stable_key("agent@alice", "agent@frank", "Subject", "Other"),
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
                    **DIRECTORY_ENV,
                    "SUPERVISOR_INBOX_AGENT_ADDRESS": "agent@frank",
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
            command[command.index("--recipient-address") + 1], "agent@alice"
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
                    **DIRECTORY_ENV,
                    "SUPERVISOR_INBOX_AGENT_ADDRESS": "agent@alice",
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
        self.assertEqual(projection["recipient_address"], "agent@frank")
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
                    **DIRECTORY_ENV,
                    "SUPERVISOR_INBOX_AGENT_ADDRESS": "agent@alice",
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
