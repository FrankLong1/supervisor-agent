from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codex_supervisor.inbox.postgres import ADAPTER_IDENTITY, CONTRACT_VERSION
from codex_supervisor.inbox_cli import _run_service_command, main, parser


AGENT_ID = "fa212e75-7581-457b-a918-4ac8bc617bbc"


class InboxCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp.name) / "state.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_disabled_status_is_local_and_does_not_open_postgres(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "codex_supervisor.inbox_cli.PostgresInboxAdapter"
            ) as adapter_class,
            redirect_stdout(output),
        ):
            code = main(["status", "--state-path", str(self.state_path)])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(result["configured"])
        self.assertFalse(result["connection_checked"])
        self.assertIsNone(result["canary_matches"])
        adapter_class.assert_not_called()

    def test_checked_status_reports_identity_and_closes_adapter(self) -> None:
        adapter = Mock()
        adapter.adapter_identity = ADAPTER_IDENTITY
        adapter.contract_version = CONTRACT_VERSION
        adapter.authenticated_identity.return_value = "frank@example.com"
        environment = {
            "SUPERVISOR_INBOX_MODE": "one-shot",
            "SUPERVISOR_INBOX_DSN": "postgresql://frank@example.com@localhost/db",
            "SUPERVISOR_INBOX_INSTANCE_ID": "frank-workstation",
            "SUPERVISOR_INBOX_AGENT_ID": AGENT_ID,
        }
        output = io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "codex_supervisor.inbox_cli.PostgresInboxAdapter",
                return_value=adapter,
            ),
            redirect_stdout(output),
        ):
            code = main(
                [
                    "status",
                    "--check-connection",
                    "--state-path",
                    str(self.state_path),
                ]
            )
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result["configured"])
        self.assertTrue(result["connection_checked"])
        self.assertEqual(result["authenticated_identity"], "frank@example.com")
        adapter.close.assert_called_once_with()

    def test_send_task_dispatch_reads_body_file_once(self) -> None:
        body_file = Path(self.temp.name) / "task.txt"
        body_file.write_text("Bounded task body", encoding="utf-8")
        args = parser().parse_args(
            [
                "send-task",
                "--recipient-address",
                "alice@example.com",
                "--subject",
                "Investigate",
                "--body-file",
                str(body_file),
                "--idempotency-key",
                "task-1",
            ]
        )
        service = Mock()
        service.send_task.return_value = {"queued": True}
        result = _run_service_command(args, service)
        self.assertEqual(result, {"queued": True})
        service.send_task.assert_called_once_with(
            recipient_address="alice@example.com",
            subject="Investigate",
            body_text="Bounded task body",
            idempotency_key="task-1",
        )


if __name__ == "__main__":
    unittest.main()
