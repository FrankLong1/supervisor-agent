from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from .claude import ConservativeClaude
from .console import serve as serve_console
from .codex import AppServerClient
from .health import exit_code, report
from .human_review_queue import render_markdown
from .inbox.config import InboxConfig, InboxMode
from .inbox.postgres import PostgresInboxAdapter
from .inbox.service import InboxService
from .models import SupervisorConfig
from .providers import build_session_adapter
from .scanner import CodexAppSnapshotInventory, UnreadScanner
from .service import (
    CONSOLE_SERVICE_NAME,
    SERVICE_NAME,
    default_unit_dir,
    install_units,
    service_status,
    uninstall_units,
)
from .state import SupervisorState, default_state_path, read_human_review_queue
from .supervisor import Supervisor
from .worker import run_worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed Codex unread-task supervisor"
    )
    parser.add_argument(
        "command",
        choices=(
            "scan-once",
            "status",
            "human-review-queue",
            "console",
            "canary-readiness",
            "reset-human-review",
            "doctor",
            "watchdog",
            "serve",
            "service-install",
            "service-status",
            "service-uninstall",
        ),
    )
    parser.add_argument("--state-path", type=Path, default=default_state_path())
    parser.add_argument(
        "--socket-path",
        type=Path,
        default=Path.home() / ".codex/app-server-control/app-server-control.sock",
    )
    parser.add_argument("--host-id", default="local")
    parser.add_argument("--thread-id")
    parser.add_argument("--inventory-snapshot", type=Path)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--grace", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--unit-dir", type=Path, default=default_unit_dir())
    parser.add_argument("--program", type=Path, default=Path(sys.argv[0]).resolve())
    parser.add_argument("--provider", default="claude")
    parser.add_argument("--provider-command", default="claude")
    parser.add_argument("--provider-model")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.interval <= 0 or args.grace < 0:
        parser.error("--interval must be positive and --grace cannot be negative")

    if args.command in {"doctor", "watchdog"}:
        report_data = report(args.state_path, args.socket_path, args.grace)
        if args.json:
            print(json.dumps(report_data, sort_keys=True))
        else:
            print(f"{report_data['status'].upper()}: Codex unread-task supervisor")
            for check in report_data["checks"]:
                print(f"{check['status'].upper():8} {check['id']}: {check['detail']}")
                if check["remediation"]:
                    print(f"         {check['remediation']}")
        return exit_code(report_data, strict=args.strict or args.command == "watchdog")

    if args.command == "serve":
        if not 5 <= args.interval <= 300:
            parser.error("serve --interval must be between 5 and 300 seconds")
        try:
            inbox_config = InboxConfig.from_env()
        except ValueError as error:
            parser.error(str(error))
        if inbox_config.mode is InboxMode.ONE_SHOT:
            parser.error(
                "serve requires SUPERVISOR_INBOX_MODE=disabled, dry-run, or poll"
            )
        if (
            inbox_config.mode is not InboxMode.DISABLED
            and float(inbox_config.poll_seconds) != args.interval
        ):
            parser.error(
                "serve --interval must match SUPERVISOR_INBOX_POLL_SECONDS"
            )
        stopping = False

        def request_stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        client = AppServerClient(args.socket_path)
        state = SupervisorState(args.state_path)

        def local_poll() -> dict[str, object]:
            scanned = UnreadScanner(client, args.host_id).scan()
            return {
                "reachable": True,
                "unread_supported": scanned.unread_supported,
                "candidate_count": len(scanned.candidates),
                "note": scanned.note,
            }

        def inbox_poll() -> dict[str, object]:
            if inbox_config.mode is InboxMode.DISABLED:
                return {"configured": False, "mode": inbox_config.mode.value}
            adapter = PostgresInboxAdapter(
                inbox_config.dsn, schema=inbox_config.schema
            )
            try:
                service = InboxService(inbox_config, adapter, state)
                if inbox_config.mode is InboxMode.DRY_RUN:
                    return {
                        "configured": True,
                        "mode": inbox_config.mode.value,
                        "handling_enabled": False,
                        **service.scan_once(),
                    }
                return {"configured": True, **service.poll_once()}
            finally:
                adapter.close()

        try:
            run_worker(
                state_path=args.state_path,
                interval=args.interval,
                stop_requested=lambda: stopping,
                local_poll=local_poll,
                inbox_poll=inbox_poll,
            )
        except RuntimeError as error:
            print(f"supervisor worker: {error}", file=sys.stderr)
            return 1
        finally:
            state.close()
        return 0

    if args.command == "service-install":
        rendered = install_units(
            args.unit_dir,
            args.program.resolve(),
            args.state_path,
            args.interval,
            args.dry_run,
        )
        if args.dry_run:
            print(
                json.dumps(
                    {"unit_dir": str(args.unit_dir), "units": rendered}, sort_keys=True
                )
            )
        else:
            enabled_units = f"{SERVICE_NAME}.service {CONSOLE_SERVICE_NAME}.service {SERVICE_NAME}-watchdog.timer"
            print(
                json.dumps(
                    {
                        "installed": sorted(rendered),
                        "unit_dir": str(args.unit_dir),
                        "enabled": False,
                        "next": f"systemctl --user enable --now {enabled_units}",
                    },
                    sort_keys=True,
                )
            )
        return 0

    if args.command == "service-status":
        code, output = service_status()
        print(output, end="" if output.endswith("\n") else "\n")
        return code

    if args.command == "service-uninstall":
        removed = uninstall_units(args.unit_dir)
        print(json.dumps({"removed": [str(path) for path in removed]}, sort_keys=True))
        return 0

    if args.command == "scan-once":
        supervisor, state = build_dry_run_supervisor(args)
        try:
            outcome = supervisor.run_once()
            print(json.dumps(outcome, sort_keys=True))
            return (
                0
                if outcome.get("ran") is not False
                and outcome.get("unread_supported") is not False
                else 1
            )
        finally:
            state.close()

    if args.command == "human-review-queue":
        print(render_markdown(read_human_review_queue(args.state_path)))
        return 0

    if args.command == "console":
        serve_console(args.state_path, args.socket_path, args.port, args.open_browser)
        return 0

    state = SupervisorState(args.state_path)
    try:
        if args.command == "status":
            print(json.dumps(state.status(), sort_keys=True))
            return 0
        if args.command == "canary-readiness":
            print(
                json.dumps(
                    {
                        "ready": False,
                        "reason": "A verified read-only hasUnreadTurn inventory and disposable-task canary evidence are required before replies can be enabled.",
                    }
                )
            )
            return 1
        if args.command == "reset-human-review":
            if not args.thread_id:
                parser.error("reset-human-review requires --thread-id")
            print(
                json.dumps(
                    {
                        "reset": state.reset_human_review(args.host_id, args.thread_id),
                        "thread_id": args.thread_id,
                    }
                )
            )
            return 0
        return 2
    finally:
        state.close()


def build_dry_run_supervisor(args):
    state = SupervisorState(args.state_path)
    client = AppServerClient(args.socket_path)
    config = SupervisorConfig(
        host_id=args.host_id, state_path=args.state_path, socket_path=args.socket_path
    )
    adapter = build_session_adapter(
        args.provider, args.provider_command, args.provider_model
    )
    inventory = (
        CodexAppSnapshotInventory(args.inventory_snapshot, args.host_id)
        if args.inventory_snapshot
        else client
    )
    return Supervisor(
        config,
        UnreadScanner(inventory, args.host_id, config.supervisor_thread_id),
        client,
        ConservativeClaude(adapter),
        state,
    ), state


if __name__ == "__main__":
    raise SystemExit(main())
