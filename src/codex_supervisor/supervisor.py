from __future__ import annotations

import fcntl
from typing import Protocol

from .claude import ConservativeClaude
from .models import DecisionKind, SupervisorConfig
from .scanner import UnreadScanner
from .state import SupervisorState


class ContextReader(Protocol):
    def read_context(self, candidate): ...


class Supervisor:
    def __init__(
        self,
        config: SupervisorConfig,
        scanner: UnreadScanner,
        codex: ContextReader,
        claude: ConservativeClaude,
        state: SupervisorState,
    ):
        self.config, self.scanner, self.codex, self.claude, self.state = (
            config,
            scanner,
            codex,
            claude,
            state,
        )

    def run_once(self) -> dict[str, int | str | bool]:
        lock_path = self.config.state_path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ran": False, "reason": "another supervisor tick is active"}
            return self._run_locked()

    def _run_locked(self) -> dict[str, int | str | bool]:
        scanned = self.scanner.scan()
        if not scanned.unread_supported:
            return {
                "ran": True,
                "candidates": 0,
                "dry_run": True,
                "unread_supported": False,
                "reason": scanned.note or "unread signal unavailable",
            }
        candidates = [
            item
            for item in scanned.candidates
            if not self.state.is_human_review(item.host_id, item.thread_id)
        ]
        if not candidates:
            return {
                "ran": True,
                "candidates": 0,
                "reason": "no eligible unread idle threads",
                "dry_run": True,
                "unread_supported": True,
            }
        processed = 0
        for candidate in candidates:
            if candidate.unread_at is None:
                self.state.record_dry_run(
                    candidate.host_id,
                    candidate.thread_id,
                    DecisionKind.HUMAN_REVIEW_NEEDED.value,
                    "unread task has no stable receipt key",
                    None,
                )
                continue
            try:
                context = self.codex.read_context(candidate)
                candidate = context.candidate
                if not context.recent_messages or context.latest_visible_result is None:
                    raise ValueError(
                        "bounded task context is empty or has no visible result"
                    )
                returned_id, decision = self.claude.decide(
                    self.state.session_id(), context
                )
                self.state.record_fable_turn(candidate.host_id, candidate.thread_id)
                if returned_id != "invalid-session":
                    self.state.set_session_id(returned_id)
            except Exception as error:
                self.state.record_dry_run(
                    candidate.host_id,
                    candidate.thread_id,
                    DecisionKind.HUMAN_REVIEW_NEEDED.value,
                    f"context or classifier failure: {error}",
                    None,
                )
                continue
            processed += 1
            self.state.record_dry_run(
                candidate.host_id,
                candidate.thread_id,
                decision.kind.value,
                decision.reason,
                decision.reply,
            )
        return {
            "ran": True,
            "candidates": len(candidates),
            "processed": processed,
            "dry_run": True,
            "unread_supported": True,
        }
