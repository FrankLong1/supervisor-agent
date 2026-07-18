from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import ScanResult, ThreadCandidate


class IdleThreadInventory(Protocol):
    """Read-only Codex inventory exposing runtime thread status."""

    def list_unarchived_threads(self) -> list[dict]: ...


class CodexAppSnapshotInventory:
    """Adapt a saved Codex-app ``list_threads`` response for one host.

    The workstation app-server does not expose client unread state. The Codex
    app does, so an app-run diagnostic may save that read-only response and
    pass it to ``supervisor scan-once --inventory-snapshot``.
    """

    def __init__(self, path: Path, host_id: str):
        self.path = path
        self.host_id = host_id

    def list_unarchived_threads(self) -> list[dict]:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("schemaVersion") != 2
            or not isinstance(value.get("threads"), list)
        ):
            raise ValueError(
                "Codex app inventory snapshot must use schemaVersion 2 and contain a threads array"
            )
        unavailable = value.get("unavailableHosts", [])
        if isinstance(unavailable, list) and any(
            item == self.host_id
            or (isinstance(item, dict) and item.get("hostId") == self.host_id)
            for item in unavailable
        ):
            raise ValueError(f"Codex app reports host {self.host_id!r} as unavailable")
        rows: list[dict] = []
        for thread in value["threads"]:
            if not isinstance(thread, dict) or thread.get("hostId") != self.host_id:
                continue
            status = thread.get("status")
            rows.append(
                {
                    "id": thread.get("id"),
                    "name": thread.get("title")
                    or thread.get("description")
                    or "Untitled task",
                    "status": {"type": status} if isinstance(status, str) else status,
                    "hasUnreadTurn": thread.get("hasUnreadTurn"),
                    "updatedAt": thread.get("updatedAt"),
                }
            )
        if value["threads"] and not rows:
            raise ValueError(
                f"Codex app snapshot contains no threads for host {self.host_id!r}"
            )
        return rows


class UnreadScanner:
    """Select only threads that Codex explicitly reports as idle and unread."""

    def __init__(
        self,
        inventory: IdleThreadInventory | None,
        host_id: str,
        own_thread_id: str | None = None,
    ):
        self.inventory, self.host_id, self.own_thread_id = (
            inventory,
            host_id,
            own_thread_id,
        )

    def scan(self) -> ScanResult:
        if self.inventory is None:
            return ScanResult(
                (), False, "no read-only Codex thread-status inventory is configured"
            )
        try:
            threads = self.inventory.list_unarchived_threads()
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            return ScanResult((), False, f"thread inventory is unavailable: {error}")
        if not isinstance(threads, list) or not all(
            isinstance(thread, dict) for thread in threads
        ):
            return ScanResult(
                (), False, "thread inventory must return a list of task objects"
            )
        if not all(isinstance(thread.get("status"), dict) for thread in threads):
            return ScanResult(
                (),
                False,
                "thread inventory does not expose runtime status for every task",
            )

        def is_idle(thread: dict) -> bool:
            return thread["status"].get("type") == "idle"

        idle_threads = [thread for thread in threads if is_idle(thread)]
        if any(
            not isinstance(thread.get("hasUnreadTurn"), bool) for thread in idle_threads
        ):
            return ScanResult(
                (), False, "idle tasks require an explicit boolean hasUnreadTurn signal"
            )
        if any(
            thread["hasUnreadTurn"] and type(thread.get("updatedAt")) is not int
            for thread in idle_threads
        ):
            return ScanResult(
                (),
                False,
                "unread idle tasks require a stable integer updatedAt receipt key",
            )
        if any(
            thread["hasUnreadTurn"]
            and (not isinstance(thread.get("id"), str) or not thread["id"].strip())
            for thread in idle_threads
        ):
            return ScanResult(
                (), False, "unread idle tasks require a non-empty string task ID"
            )
        candidates = [
            ThreadCandidate(
                self.host_id,
                str(thread["id"]),
                str(thread.get("name") or thread.get("title") or "Untitled task"),
                thread.get("updatedAt"),
                thread.get("activeTurnId"),
            )
            for thread in idle_threads
            if thread["hasUnreadTurn"] and str(thread.get("id")) != self.own_thread_id
        ]
        return ScanResult(
            tuple(sorted(candidates, key=lambda item: item.unread_at or 0)), True
        )
