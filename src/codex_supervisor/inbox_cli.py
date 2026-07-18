from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .inbox.config import InboxConfig, InboxMode
from .inbox.postgres import ADAPTER_IDENTITY, CONTRACT_VERSION, PostgresInboxAdapter
from .inbox.service import CANARY_EVIDENCE_ID, HANDLER_IDENTITY, InboxService
from .state import SupervisorState, default_state_path
from .worker import WorkerLease


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="One-shot shared inbox supervisor")
    command.add_argument(
        "command",
        choices=("status", "scan-once", "canary", "run-once", "send-task"),
    )
    command.add_argument("--state-path", type=Path, default=default_state_path())
    command.add_argument("--limit", type=int, default=20)
    command.add_argument("--check-connection", action="store_true")
    command.add_argument("--delivery-id")
    command.add_argument("--sender-observed-reply-id")
    command.add_argument("--recipient-address")
    command.add_argument("--subject")
    command.add_argument("--body-text")
    command.add_argument("--body-file", type=Path)
    command.add_argument("--idempotency-key")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = InboxConfig.from_env()
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    state = SupervisorState(args.state_path)
    adapter = None
    try:
        if args.command == "status" and not args.check_connection:
            result = {
                "configured": config.mode is not InboxMode.DISABLED,
                "mode": config.mode.value,
                "database_target": config.masked_target,
                "adapter_identity": ADAPTER_IDENTITY,
                "contract_version": CONTRACT_VERSION,
                "instance_id": config.instance_id,
                "agent_id": config.agent_id,
                "connection_checked": False,
                "canary_matches": None,
                **state.inbox_status(),
            }
            print(json.dumps(result, sort_keys=True))
            return 0
        if config.mode is InboxMode.DISABLED:
            raise ValueError("inbox mode is disabled")
        adapter = PostgresInboxAdapter(config.dsn, schema=config.schema)
        service = InboxService(config, adapter, state)
        if args.command == "status":
            identity = adapter.authenticated_identity()
            result = {
                "configured": True,
                "mode": config.mode.value,
                "database_target": config.masked_target,
                "adapter_identity": adapter.adapter_identity,
                "contract_version": adapter.contract_version,
                "instance_id": config.instance_id,
                "agent_id": config.agent_id,
                "connection_checked": True,
                "authenticated_identity": identity,
                "canary_matches": state.inbox_canary_matches(
                    evidence_id=CANARY_EVIDENCE_ID,
                    contract_version=adapter.contract_version,
                    adapter_identity=adapter.adapter_identity,
                    principal_identity=identity,
                    instance_id=config.instance_id,
                    handler_identity=HANDLER_IDENTITY,
                ),
                **state.inbox_status(),
            }
        elif args.command == "scan-once":
            result = service.scan_once(args.limit)
        elif args.command == "canary":
            if not args.delivery_id:
                raise ValueError("canary requires --delivery-id")
            with WorkerLease(args.state_path):
                result = service.canary(
                    args.delivery_id, args.sender_observed_reply_id
                )
        elif args.command == "run-once":
            with WorkerLease(args.state_path):
                result = service.run_once()
        else:
            if (
                not args.recipient_address
                or not args.subject
                or not args.idempotency_key
            ):
                raise ValueError(
                    "send-task requires --recipient-address, --subject, and --idempotency-key"
                )
            if bool(args.body_text) == bool(args.body_file):
                raise ValueError(
                    "send-task requires exactly one of --body-text or --body-file"
                )
            try:
                body_text = (
                    args.body_file.read_text(encoding="utf-8")
                    if args.body_file
                    else args.body_text
                )
            except (OSError, UnicodeError) as error:
                raise ValueError("task body file is not readable UTF-8") from error
            result = service.send_task(
                recipient_address=args.recipient_address,
                subject=args.subject,
                body_text=body_text,
                idempotency_key=args.idempotency_key,
            )
        print(json.dumps(result, sort_keys=True))
        if args.command == "canary" and not result.get("canary_enrolled"):
            return 1
        return 0
    except (ValueError, RuntimeError) as error:
        print(
            json.dumps({"ok": False, "error": str(error)}, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    finally:
        if adapter is not None:
            adapter.close()
        state.close()
