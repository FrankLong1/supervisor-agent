from __future__ import annotations

from typing import Protocol

from .models import ScanResult, ThreadCandidate


class VerifiedUnreadInventory(Protocol):
    """A read-only inventory proven by the disposable-thread canary."""

    def list_unarchived_threads(self) -> list[dict]: ...


class UnreadScanner:
    """First deterministic gate; unavailable/unverified unread data means no scan."""

    def __init__(self, inventory: VerifiedUnreadInventory | None, host_id: str, own_thread_id: str | None = None):
        self.inventory, self.host_id, self.own_thread_id = inventory, host_id, own_thread_id

    def scan(self) -> ScanResult:
        if self.inventory is None:
            return ScanResult((), False, "no verified read-only unread inventory is configured")
        threads = self.inventory.list_unarchived_threads()
        if not all("hasUnreadTurn" in thread for thread in threads):
            return ScanResult((), False, "thread inventory does not expose hasUnreadTurn for every task")
        candidates = [
            ThreadCandidate(self.host_id, str(thread["id"]), str(thread.get("name") or thread.get("title") or "Untitled task"), thread.get("unreadAt"), thread.get("activeTurnId"))
            for thread in threads
            if thread.get("hasUnreadTurn") is True and str(thread.get("id")) != self.own_thread_id
        ]
        return ScanResult(tuple(sorted(candidates, key=lambda item: item.unread_at or 0)), True)
