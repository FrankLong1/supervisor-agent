from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

from .claude import ClaudeCodeSession, ConservativeClaude
from .console import serve as serve_console
from .codex import AppServerClient
from .health import exit_code, report, write_heartbeat
from .human_review_queue import render_markdown
from .models import SupervisorConfig
from .scanner import UnreadScanner
from .service import default_unit_dir, install_units, service_status, uninstall_units
from .state import SupervisorState, default_state_path, read_human_review_queue
from .supervisor import Supervisor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Codex unread-task supervisor")
    parser.add_argument("command", choices=("scan-once", "status", "human-review-queue", "console", "canary-readiness", "reset-human-review", "doctor", "watchdog", "serve", "service-install", "service-status", "service-uninstall"))
    parser.add_argument("--state-path", type=Path, default=default_state_path())
    parser.add_argument("--socket-path", type=Path, default=Path.home() / ".codex/app-server-control/app-server-control.sock")
    parser.add_argument("--host-id", default="local")
    parser.add_argument("--thread-id")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--grace", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--unit-dir", type=Path, default=default_unit_dir())
    parser.add_argument("--program", type=Path, default=Path(sys.argv[0]).resolve())
    parser.add_argument("--fable-command", default="claude")
    parser.add_argument("--fable-model", default="fable")
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
        stopping = False

        def request_stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        supervisor, state = build_live_supervisor(args)
        try:
            while not stopping:
                write_heartbeat(args.state_path, result="in_progress", interval=args.interval, detail="supervisor cycle in progress")
                outcome = supervisor.run_once()
                write_heartbeat(args.state_path, result="ok", interval=args.interval, detail=json.dumps(outcome, sort_keys=True))
                for _ in range(max(1, int(args.interval * 5))):
                    if stopping: break
                    time.sleep(0.2)
        finally:
            state.close()
        return 0

    if args.command == "service-install":
        rendered = install_units(args.unit_dir, args.program.resolve(), args.state_path, args.interval, args.dry_run)
        if args.dry_run:
            print(json.dumps({"unit_dir": str(args.unit_dir), "units": rendered}, sort_keys=True))
        else:
            print(json.dumps({"installed": sorted(rendered), "unit_dir": str(args.unit_dir), "enabled": False, "next": f"systemctl --user enable --now codex-unread-supervisor.service codex-unread-supervisor-watchdog.timer"}, sort_keys=True))
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
        supervisor, state = build_live_supervisor(args)
        try:
            print(json.dumps(supervisor.run_once(), sort_keys=True))
            return 0
        finally:
            state.close()

    if args.command == "human-review-queue":
        print(render_markdown(read_human_review_queue(args.state_path))); return 0

    if args.command == "console":
        serve_console(args.state_path, args.socket_path, args.port, args.open_browser); return 0

    state = SupervisorState(args.state_path)
    try:
        if args.command == "status":
            print(json.dumps(state.status(), sort_keys=True)); return 0
        if args.command == "canary-readiness":
            print(json.dumps({"ready": False, "reason": "A verified read-only hasUnreadTurn inventory and disposable-task canary evidence are required before replies can be enabled."})); return 1
        if args.command == "reset-human-review":
            if not args.thread_id: parser.error("reset-human-review requires --thread-id")
            print(json.dumps({"reset": state.reset_human_review(args.host_id, args.thread_id), "thread_id": args.thread_id})); return 0
        return 2
    finally:
        state.close()


def build_live_supervisor(args):
    state = SupervisorState(args.state_path)
    client = AppServerClient(args.socket_path)
    config = SupervisorConfig(host_id=args.host_id, state_path=args.state_path, socket_path=args.socket_path, shadow_mode=False, allow_replies=True)
    return Supervisor(config, UnreadScanner(client, args.host_id, config.supervisor_thread_id), client, ConservativeClaude(ClaudeCodeSession(args.fable_command, args.fable_model)), state), state


if __name__ == "__main__":
    raise SystemExit(main())
