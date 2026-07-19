from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .codex import AppServerClient
from .inbox.config import InboxConfig, InboxMode
from .inbox.postgres import ADAPTER_IDENTITY, CONTRACT_VERSION, PostgresInboxAdapter
from .inbox.service import CANARY_EVIDENCE_ID, HANDLER_IDENTITY, InboxService
from .state import SupervisorState, default_state_path
from .worker import WorkerLease


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="One-shot shared inbox supervisor")
    command.add_argument(
        "command",
        choices=(
            "status",
            "scan-once",
            "canary",
            "run-once",
            "send-canary",
            "send-task",
        ),
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
    command.add_argument("--workspace-key")
    command.add_argument("--source-codex-thread-id")
    command.add_argument("--expected-result")
    command.add_argument(
        "--socket-path", type=Path,
        default=Path.home() / ".codex/app-server-control/app-server-control.sock",
    )
    return command


def _status_result(
    config: InboxConfig,
    state: SupervisorState,
    adapter: PostgresInboxAdapter | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "configured": config.mode is not InboxMode.DISABLED,
        "mode": config.mode.value,
        "database_target": config.masked_target,
        "adapter_identity": ADAPTER_IDENTITY,
        "contract_version": CONTRACT_VERSION,
        "instance_id": config.instance_id,
        "agent_id": config.agent_id,
        "connection_checked": adapter is not None,
        "canary_matches": None,
        "execution_mode": config.execution_mode.value,
        "workspace_key": config.workspace_key,
        "workspace_configured": config.workspace_path is not None,
        **state.inbox_status(),
    }
    if adapter is None:
        return result
    identity = adapter.authenticated_identity()
    result.update(
        {
            "adapter_identity": adapter.adapter_identity,
            "contract_version": adapter.contract_version,
            "authenticated_identity": identity,
            "canary_matches": state.inbox_canary_matches(
                evidence_id=CANARY_EVIDENCE_ID,
                contract_version=adapter.contract_version,
                adapter_identity=adapter.adapter_identity,
                principal_identity=identity,
                instance_id=config.instance_id,
                handler_identity=HANDLER_IDENTITY,
            ),
        }
    )
    return result


def _run_service_command(
    args: argparse.Namespace, service: InboxService
) -> dict[str, object]:
    if args.command == "scan-once":
        return service.scan_once(args.limit)
    if args.command == "canary":
        if not args.delivery_id:
            raise ValueError("canary requires --delivery-id")
        with WorkerLease(args.state_path):
            return service.canary(args.delivery_id, args.sender_observed_reply_id)
    if args.command == "run-once":
        with WorkerLease(args.state_path):
            return service.run_once()
    if args.command == "send-canary":
        if not args.recipient_address or not args.idempotency_key:
            raise ValueError(
                "send-canary requires --recipient-address and --idempotency-key"
            )
        return service.send_canary(
            recipient_address=args.recipient_address,
            idempotency_key=args.idempotency_key,
        )
    if not args.recipient_address or not args.subject or not args.idempotency_key:
        raise ValueError(
            "send-task requires --recipient-address, --subject, and --idempotency-key"
        )
    if bool(args.body_text) == bool(args.body_file):
        raise ValueError("send-task requires exactly one of --body-text or --body-file")
    try:
        body_text = (
            args.body_file.read_text(encoding="utf-8")
            if args.body_file
            else args.body_text
        )
    except (OSError, UnicodeError) as error:
        raise ValueError("task body file is not readable UTF-8") from error
    task_kwargs: dict[str, object] = dict(
        recipient_address=args.recipient_address,
        subject=args.subject,
        body_text=body_text,
        idempotency_key=args.idempotency_key,
    )
    if args.workspace_key is not None:
        task_kwargs["workspace_key"] = args.workspace_key
    if args.source_codex_thread_id is not None:
        task_kwargs["source_codex_thread_id"] = args.source_codex_thread_id
    if args.expected_result is not None:
        task_kwargs["expected_result"] = args.expected_result
    return service.send_task(**task_kwargs)


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
            print(json.dumps(_status_result(config, state), sort_keys=True))
            return 0
        if config.mode is InboxMode.DISABLED:
            raise ValueError("inbox mode is disabled")
        adapter = PostgresInboxAdapter(config.dsn, schema=config.schema)
        if args.command == "status":
            result = _status_result(config, state, adapter)
        else:
            service = InboxService(
                config, adapter, state, AppServerClient(args.socket_path)
            )
            result = _run_service_command(args, service)
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
