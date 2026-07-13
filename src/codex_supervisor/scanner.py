from __future__ import annotations

from typing import Protocol

from .models import ScanResult, ThreadCandidate


class IdleThreadInventory(Protocol):
    """Read-only Codex inventory exposing runtime thread status."""

    def list_unarchived_threads(self) -> list[dict]: ...


class UnreadScanner:
    """Select idle threads until Fable records a terminal human-review stop.

    ``idle`` is the product's explicit work signal: every idle thread is
    reviewed unless it already has a durable HUMAN_REVIEW_NEEDED marker. A
    later agent result updates the thread receipt and makes it eligible again.
    """

    def __init__(self, inventory: IdleThreadInventory | None, host_id: str, own_thread_id: str | None = None):
        self.inventory, self.host_id, self.own_thread_id = inventory, host_id, own_thread_id

    def scan(self) -> ScanResult:
        if self.inventory is None:
            return ScanResult((), False, "no read-only Codex thread-status inventory is configured")
        threads = self.inventory.list_unarchived_threads()
        if not all(isinstance(thread.get("status"), dict) for thread in threads):
            return ScanResult((), False, "thread inventory does not expose runtime status for every task")
        def is_idle(thread: dict) -> bool:
            return thread["status"].get("type") == "idle"
        if any(is_idle(thread) and not isinstance(thread.get("updatedAt"), int) for thread in threads):
            return ScanResult((), False, "idle tasks require a stable integer updatedAt receipt key")
        candidates = [
            ThreadCandidate(self.host_id, str(thread["id"]), str(thread.get("name") or thread.get("title") or "Untitled task"), thread.get("updatedAt"), thread.get("activeTurnId"))
            for thread in threads
            if is_idle(thread) and str(thread.get("id")) != self.own_thread_id
        ]
        return ScanResult(tuple(sorted(candidates, key=lambda item: item.unread_at or 0)), True)
