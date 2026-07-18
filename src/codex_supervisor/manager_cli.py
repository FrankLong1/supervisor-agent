from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

from .manager import Manager, ManagerStore, PROVIDERS, Session, default_manager_state_path


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Manage interactive Codex and Claude sessions")
    command.add_argument("command", choices=("codex", "claude", "status", "stop", "logs", "doctor"))
    command.add_argument("session_id", nargs="?")
    command.add_argument("--workspace", type=Path, default=Path.cwd())
    command.add_argument("--task")
    command.add_argument("--state-path", type=Path, default=default_manager_state_path())
    command.add_argument("--all", action="store_true")
    command.add_argument("--once", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--force", action="store_true")
    command.add_argument("--follow", action="store_true")
    return command


def _render(sessions: list[Session], as_json: bool) -> None:
    if as_json:
        print(json.dumps([session.__dict__ for session in sessions], sort_keys=True))
        return
    if not sessions:
        print("No active Supervisor session in this workspace.")
        return
    for session in sessions:
        print(
            "Supervisor status\n"
            f"  session:   {session.id}\n  provider:  {session.provider}\n"
            f"  workspace: {session.workspace}\n  phase:     {session.phase}\n"
            f"  pid:       {session.pid}\n  started:   {session.started_at}\n"
            f"  actions:   supervisor logs {session.id} | supervisor stop {session.id}"
        )


def _session(store: ManagerStore, session_id: str | None, workspace: Path) -> Session | None:
    return store.load(session_id) if session_id else store.active_for_workspace(workspace.resolve())


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = ManagerStore(args.state_path)
    manager = Manager(store)
    workspace = args.workspace.resolve()
    if args.command in PROVIDERS:
        session = manager.start(args.command, workspace, args.task)
        print(f"Supervisor session {session.id} is running {session.provider} in {session.workspace}.", flush=True)

        def request_stop(_signum, _frame):
            print("\nStopping managed session…", flush=True)
            manager.stop(session)

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        try:
            while session.pid is not None:
                try:
                    _pid, status = __import__("os").waitpid(session.pid, 0)
                    return_code = __import__("os").waitstatus_to_exitcode(status)
                    manager.complete(session, return_code)
                    return return_code
                except ChildProcessError:
                    manager.wait_for_exit(session)
                    manager.complete(session, 0)
                    return 0
        finally:
            pass
    if args.command == "status":
        def sessions() -> list[Session]:
            return list(store.all_active()) if args.all else ([item] if (item := store.active_for_workspace(workspace)) else [])
        current = sessions()
        _render(current, args.json)
        if not current:
            return 1
        if args.once or args.json or not sys.stdout.isatty():
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
        print(manager.stop(session, args.force))
        return 0
    if args.command == "logs":
        session = _session(store, args.session_id, workspace)
        if not session:
            print("No managed session found.", file=sys.stderr)
            return 1
        path = store.log_path(session.id)
        if path.exists():
            print(path.read_text(encoding="utf-8"), end="")
        return 0
    if args.command == "doctor":
        report = {name: {"command": provider.command, "installed": bool(__import__("shutil").which(provider.command))} for name, provider in PROVIDERS.items()}
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print("Supervisor doctor")
            for name, item in report.items(): print(f"  {name}: {'installed' if item['installed'] else 'missing'} ({item['command']})")
            print(f"  state directory: {store.root}")
        return 0 if all(item["installed"] for item in report.values()) else 1
    return 2
