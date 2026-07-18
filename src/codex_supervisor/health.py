from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal


HealthStatus = Literal["ok", "degraded", "failed", "unknown"]


@dataclass(frozen=True)
class Check:
    id: str
    status: HealthStatus
    detail: str
    remediation: str | None = None


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def heartbeat_path(state_path: Path) -> Path:
    return state_path.with_suffix(".heartbeat.json")


def write_heartbeat(state_path: Path, *, result: str, interval: float, detail: str) -> dict[str, object]:
    """Atomically publish progress without touching supervisor decision state."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "pid": os.getpid(),
        "finished_at": now_iso(),
        "result": result,
        "detail": detail,
        "interval_seconds": interval,
    }
    path = heartbeat_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return payload


def read_heartbeat(state_path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(heartbeat_path(state_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def heartbeat_check(state_path: Path, grace_seconds: float) -> Check:
    heartbeat = read_heartbeat(state_path)
    if heartbeat is None:
        return Check("heartbeat", "degraded", "No controller or compatibility service has written a heartbeat.", "Start the controller or heartbeat service and re-run doctor.")
    finished_at = parse_timestamp(heartbeat.get("finished_at"))
    if finished_at is None:
        return Check("heartbeat", "failed", "Heartbeat timestamp is invalid.", "Stop the service, inspect its logs, then remove the corrupt heartbeat file.")
    interval = heartbeat.get("interval_seconds")
    if not isinstance(interval, (int, float)) or interval <= 0:
        return Check("heartbeat", "failed", "Heartbeat interval is invalid.", "Reinstall the service with a positive interval.")
    age = (datetime.now(UTC) - finished_at).total_seconds()
    limit = float(interval) + grace_seconds
    if age > limit:
        return Check("heartbeat", "failed", f"Heartbeat is stale ({age:.1f}s old; limit {limit:.1f}s).", "Inspect `service-status` and journal logs; do not kill an active worker automatically.")
    result = heartbeat.get("result")
    if result != "ok":
        return Check("heartbeat", "degraded", f"Latest worker tick completed with result {result!r}.", "Inspect the service logs before enabling any reply capability.")
    return Check("heartbeat", "ok", f"Heartbeat is fresh ({age:.1f}s old).")


def state_check(state_path: Path) -> Check:
    if not state_path.exists():
        return Check("state", "degraded", f"State database does not exist yet: {state_path}", "Run `supervisor scan-once` to create dry-run audit state.")
    try:
        uri = f"file:{state_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            db.execute("SELECT count(*) FROM human_review_tasks").fetchone()
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.Error as error:
        return Check("state", "failed", f"State database cannot be read: {error}", "Stop the service and restore or repair the state database.")
    if integrity != "ok":
        return Check("state", "failed", f"SQLite quick_check returned {integrity!r}.", "Stop the service and repair the state database before continuing.")
    return Check("state", "ok", f"State database is readable: {state_path}")


def socket_check(socket_path: Path) -> Check:
    try:
        mode = socket_path.stat().st_mode
    except FileNotFoundError:
        return Check("codex_socket", "degraded", f"Codex socket is absent: {socket_path}", "Start Codex and verify the configured app-server socket path.")
    except OSError as error:
        return Check("codex_socket", "failed", f"Cannot inspect Codex socket: {error}", "Fix socket permissions or the configured path.")
    if not stat.S_ISSOCK(mode):
        return Check("codex_socket", "failed", f"Configured socket path is not a Unix socket: {socket_path}", "Correct the socket path; do not substitute a guessed transport.")
    return Check("codex_socket", "ok", f"Unix socket exists: {socket_path}")


def service_manager_check() -> Check:
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "is-active", "codex-unread-supervisor.service"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return Check("service_manager", "unknown", "systemctl is unavailable; this is not a Linux systemd user session.", "Use foreground `serve` or install a supported platform integration.")
    status = completed.stdout.strip() or completed.stderr.strip() or "unknown"
    if completed.returncode == 0 and status == "active":
        return Check("service_manager", "ok", "systemd user service is active.")
    return Check("service_manager", "degraded", f"systemd user service is not active ({status}).", "Run `codex-unread-supervisor service-status` and inspect the user journal.")


def report(state_path: Path, socket_path: Path, grace_seconds: float) -> dict[str, object]:
    checks = [state_check(state_path), socket_check(socket_path), heartbeat_check(state_path, grace_seconds), service_manager_check()]
    severity = {"ok": 0, "unknown": 1, "degraded": 2, "failed": 3}
    overall = max(checks, key=lambda check: severity[check.status]).status
    return {"status": overall, "checked_at": now_iso(), "checks": [asdict(check) for check in checks]}


def exit_code(report_data: dict[str, object], strict: bool) -> int:
    status = report_data["status"]
    if status == "failed":
        return 2
    if strict and status != "ok":
        return 1
    return 0


def serve_heartbeat_only(state_path: Path, interval: float, stop_requested) -> None:
    """Keep service liveness observable until a reviewed production adapter exists.

    This intentionally does not call Supervisor.run_once: the repository has no
    verified unread inventory or production Claude/Fable adapter to invoke.
    """
    while not stop_requested():
        write_heartbeat(state_path, result="ok", interval=interval, detail="scheduler ready; scan adapter is intentionally disabled")
        deadline = time.monotonic() + interval
        while not stop_requested() and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
