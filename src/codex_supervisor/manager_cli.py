from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .cli import main as unread_supervisor_main
from .inbox_cli import main as inbox_main
from .manager import (
    ACTIVE_PHASES,
    Manager,
    ManagerStore,
    ManagedRuntime,
    PROVIDERS,
    Session,
    default_manager_state_path,
    session_matches_controller,
    session_matches_provider,
)
from .state import default_state_path


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Manage interactive Codex and Claude sessions"
    )
    command.add_argument(
        "command",
        choices=("codex", "claude", "scan-once", "status", "stop", "logs", "doctor"),
    )
    command.add_argument("session_id", nargs="?")
    command.add_argument("--workspace", type=Path, default=Path.cwd())
    command.add_argument("--task")
    command.add_argument(
        "--state-path",
        type=Path,
        default=default_manager_state_path(),
        help="managed-session state directory",
    )
    command.add_argument(
        "--workflow-state-path",
        type=Path,
        default=default_state_path(),
        help="unread-workflow SQLite state",
    )
    command.add_argument(
        "--socket-path",
        type=Path,
        default=Path.home() / ".codex/app-server-control/app-server-control.sock",
    )
    command.add_argument("--host-id", default="local")
    command.add_argument("--inventory-snapshot", type=Path)
    command.add_argument("--decision-provider", default="claude")
    command.add_argument("--decision-provider-command", default="claude")
    command.add_argument("--decision-provider-model")
    command.add_argument("--all", action="store_true")
    command.add_argument("--watch", action="store_true")
    command.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    command.add_argument("--json", action="store_true")
    command.add_argument("--force", action="store_true")
    command.add_argument("--follow", action="store_true")
    return command


def _timestamp_age(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - parsed).total_seconds())


def _projection(session: Session) -> dict[str, object]:
    controller_live = session_matches_controller(session)
    provider_live = session_matches_provider(session)
    heartbeat_age = _timestamp_age(session.heartbeat_at)
    if session.phase == "attention":
        health = "attention"
    elif session.phase in ACTIVE_PHASES:
        health = (
            "healthy"
            if controller_live
            and provider_live
            and heartbeat_age is not None
            and heartbeat_age <= 5
            else "attention"
        )
    else:
        health = "stopped"
    return {
        "session_id": session.id,
        "provider": session.provider,
        "workspace": session.workspace,
        "phase": session.phase,
        "health": health,
        "controller": {
            "pid": session.controller_pid,
            "identity_verified": controller_live,
        },
        "provider_process": {
            "pid": session.provider_pid,
            "identity_verified": provider_live,
        },
        "heartbeat_at": session.heartbeat_at,
        "heartbeat_age_seconds": None
        if heartbeat_age is None
        else round(heartbeat_age, 3),
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "reason": session.reason,
        "actions": [f"supervisor logs {session.id}", f"supervisor stop {session.id}"],
    }


def _render(sessions: list[Session], as_json: bool) -> None:
    projected = [_projection(session) for session in sessions]
    if as_json:
        print(json.dumps(projected, sort_keys=True))
        return
    if not projected:
        print("No Supervisor session in this workspace.")
        return
    for item in projected:
        heartbeat_age = item["heartbeat_age_seconds"]
        heartbeat = "unknown" if heartbeat_age is None else f"{heartbeat_age:.1f}s ago"
        print(
            "Supervisor status\n"
            f"  session:    {item['session_id']}\n"
            f"  provider:   {item['provider']} (pid {item['provider_process']['pid']})\n"
            f"  workspace:  {item['workspace']}\n"
            f"  phase:      {item['phase']}\n"
            f"  health:     {item['health']}\n"
            f"  heartbeat:  {heartbeat}\n"
            f"  reason:     {item['reason'] or '-'}\n"
            f"  actions:    {' | '.join(item['actions'])}"
        )


def _session(
    store: ManagerStore, session_id: str | None, workspace: Path
) -> Session | None:
    return (
        store.load(session_id)
        if session_id
        else store.active_for_workspace(workspace.resolve())
    )


def _scan_once(args: argparse.Namespace) -> int:
    delegated = [
        "scan-once",
        "--state-path",
        str(args.workflow_state_path),
        "--socket-path",
        str(args.socket_path),
        "--host-id",
        args.host_id,
        "--provider",
        args.decision_provider,
        "--provider-command",
        args.decision_provider_command,
    ]
    if args.decision_provider_model:
        delegated.extend(["--provider-model", args.decision_provider_model])
    if args.inventory_snapshot:
        delegated.extend(["--inventory-snapshot", str(args.inventory_snapshot)])
    return unread_supervisor_main(delegated)


def _follow_log(store: ManagerStore, session: Session) -> int:
    path = store.log_path(session.id)
    position = 0
    try:
        while True:
            if path.exists():
                with path.open("r", encoding="utf-8") as log:
                    log.seek(position)
                    chunk = log.read()
                    position = log.tell()
                if chunk:
                    print(chunk, end="", flush=True)
            current = store.load(session.id)
            if current is None or not session_matches_controller(current):
                return 0
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    if effective_argv and effective_argv[0] == "inbox":
        return inbox_main(effective_argv[1:])
    args = parser().parse_args(argv)
    if args.command == "scan-once":
        return _scan_once(args)

    store = ManagerStore(args.state_path)
    manager = Manager(store)
    workspace = args.workspace.resolve()
    if args.command in PROVIDERS:
        try:
            session = manager.start(args.command, workspace, args.task)
        except (RuntimeError, ValueError) as error:
            print(f"supervisor: {error}", file=sys.stderr)
            return 1
        print(
            f"Supervisor session {session.id} is running {session.provider} in {session.workspace}.",
            flush=True,
        )
        runtime = ManagedRuntime(manager)

        def request_stop(_signum, _frame):
            runtime.request_stop()

        previous_int = signal.signal(signal.SIGINT, request_stop)
        previous_term = signal.signal(signal.SIGTERM, request_stop)
        try:
            return runtime.run(session)
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)
    if args.command == "status":

        def sessions() -> list[Session]:
            selected = (
                list(store.all_active())
                if args.all
                else (
                    [item]
                    if (item := _session(store, args.session_id, workspace))
                    else []
                )
            )
            return [manager.refresh(item) for item in selected]

        current = sessions()
        _render(current, args.json)
        if not current:
            return 1
        if not args.watch or args.once or args.json or not sys.stdout.isatty():
            return 0
        try:
            while True:
                time.sleep(1)
                print("\033[2J\033[H", end="")
                _render(sessions(), False)
        except KeyboardInterrupt:
            return 0
    if args.command == "stop":
        session = _session(store, args.session_id, workspace)
        if not session:
            print("No managed session found.", file=sys.stderr)
            return 1
        try:
            print(manager.stop(session, args.force))
        except RuntimeError as error:
            print(f"supervisor: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "logs":
        session = _session(store, args.session_id, workspace)
        if not session:
            print("No managed session found.", file=sys.stderr)
            return 1
        if args.follow:
            return _follow_log(store, session)
        path = store.log_path(session.id)
        if path.exists():
            print(path.read_text(encoding="utf-8"), end="")
        return 0
    if args.command == "doctor":
        report = {
            name: {
                "command": provider.command,
                "installed": bool(__import__("shutil").which(provider.command)),
            }
            for name, provider in PROVIDERS.items()
        }
        report["state"] = {
            "directory": str(store.root),
            "writable": store.root.is_dir()
            and __import__("os").access(store.root, __import__("os").W_OK),
        }
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print("Supervisor doctor")
            for name in PROVIDERS:
                item = report[name]
                print(
                    f"  {name}: {'installed' if item['installed'] else 'missing'} ({item['command']})"
                )
            print(f"  state directory: {store.root}")
        return 0 if report["state"]["writable"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
