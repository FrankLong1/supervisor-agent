from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import fcntl
import json
from pathlib import Path
import time
from typing import Any

from .health import write_heartbeat


class WorkerLease(AbstractContextManager["WorkerLease"]):
    """Hold the one scheduler lease for the lifetime of the foreground worker."""

    def __init__(self, state_path: Path):
        self.path = state_path.with_suffix(".worker.lock")
        self.handle = None

    def __enter__(self) -> "WorkerLease":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.handle.close()
            self.handle = None
            raise RuntimeError("another supervisor worker is already active") from error
        return self

    def __exit__(self, *_args: object) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def _safe_tick(source: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"source": source, "ok": True, **operation()}
    except Exception as error:
        # Heartbeats and service logs must not accidentally serialize a DSN or
        # server-provided payload. The exception class is enough to diagnose
        # the failing source before an operator runs its explicit status command.
        return {"source": source, "ok": False, "error_type": type(error).__name__}


def combined_tick(
    local_poll: Callable[[], dict[str, Any]],
    inbox_poll: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    local = _safe_tick("local_app_server", local_poll)
    inbox = _safe_tick("cloud_sql_inbox", inbox_poll)
    handling_blocked = bool(
        inbox.get("ok")
        and inbox.get("configured")
        and inbox.get("mode") == "poll"
        and not inbox.get("handling_enabled")
    )
    return {
        "result": "degraded"
        if not local["ok"] or not inbox["ok"] or handling_blocked
        else "ok",
        "local": local,
        "inbox": inbox,
    }


def heartbeat_detail(tick: dict[str, Any]) -> str:
    detail = json.dumps(tick, sort_keys=True, separators=(",", ":"))
    return detail[:4096]


def run_worker(
    *,
    state_path: Path,
    interval: float,
    stop_requested: Callable[[], bool],
    local_poll: Callable[[], dict[str, Any]],
    inbox_poll: Callable[[], dict[str, Any]],
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
) -> None:
    if interval <= 0:
        raise ValueError("worker interval must be positive")
    with WorkerLease(state_path):
        while not stop_requested():
            tick = combined_tick(local_poll, inbox_poll)
            write_heartbeat(
                state_path,
                result=str(tick["result"]),
                interval=interval,
                detail=heartbeat_detail(tick),
            )
            deadline = clock() + interval
            while not stop_requested() and clock() < deadline:
                wait(min(0.2, max(0.0, deadline - clock())))
