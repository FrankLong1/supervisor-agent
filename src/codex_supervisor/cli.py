from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .state import SupervisorState, default_state_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Codex unread-task supervisor")
    parser.add_argument("command", choices=("scan-once", "status", "canary-readiness", "reset-human-review"))
    parser.add_argument("--state-path", type=Path, default=default_state_path())
    parser.add_argument("--host-id", default="local")
    parser.add_argument("--thread-id")
    args = parser.parse_args(argv)
    state = SupervisorState(args.state_path)
    try:
        if args.command == "status":
            print(json.dumps(state.status(), sort_keys=True)); return 0
        if args.command == "canary-readiness":
            print(json.dumps({"ready": False, "reason": "A verified read-only hasUnreadTurn inventory and disposable-task canary evidence are required before replies can be enabled."})); return 1
        if args.command == "reset-human-review":
            if not args.thread_id: parser.error("reset-human-review requires --thread-id")
            print(json.dumps({"reset": state.reset_human_review(args.host_id, args.thread_id), "thread_id": args.thread_id})); return 0
        print("scan-once is disabled: no reviewed Claude/Fable session-resume adapter or verified unread inventory is configured", file=sys.stderr)
        return 2
    finally:
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())
