from __future__ import annotations

import fcntl
from typing import Protocol

from .claude import ConservativeClaude
from .models import DecisionKind, SupervisorConfig
from .scanner import UnreadScanner
from .state import SupervisorState


class ContextReader(Protocol):
    def read_context(self, candidate): ...
    def send_reply(self, candidate, reply: str) -> None: ...


class Supervisor:
    def __init__(self, config: SupervisorConfig, scanner: UnreadScanner, codex: ContextReader, claude: ConservativeClaude, state: SupervisorState):
        self.config, self.scanner, self.codex, self.claude, self.state = config, scanner, codex, claude, state

    def run_once(self) -> dict[str, int | str | bool]:
        lock_path = self.config.state_path.with_suffix(".lock"); lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return {"ran": False, "reason": "another supervisor tick is active"}
            return self._run_locked()

    def _run_locked(self) -> dict[str, int | str | bool]:
        scanned = self.scanner.scan()
        if not scanned.unread_supported: return {"ran": True, "candidates": 0, "reason": scanned.note or "unread signal unavailable"}
        candidates = [item for item in scanned.candidates if not self.state.is_human_review(item.host_id, item.thread_id)]
        if not candidates: return {"ran": True, "candidates": 0, "reason": "no eligible unread tasks"}
        processed = 0
        for candidate in candidates:
            returned_id, decision = self.claude.decide(self.state.session_id(), self.codex.read_context(candidate))
            if returned_id != "invalid-session": self.state.set_session_id(returned_id)
            processed += 1
            if self.config.shadow_mode:
                self.state.record_shadow(candidate.host_id, candidate.thread_id, decision.kind.value, decision.reason, decision.reply); continue
            if decision.kind is DecisionKind.HUMAN_REVIEW_NEEDED:
                self.state.mark_human_review(candidate.host_id, candidate.thread_id, decision.reason); continue
            if not self.config.mutation_allowed():
                self.state.mark_human_review(candidate.host_id, candidate.thread_id, "reply blocked: canary and explicit enablement required"); continue
            try: self.codex.send_reply(candidate, decision.reply or "")
            except Exception as error: self.state.mark_human_review(candidate.host_id, candidate.thread_id, f"ambiguous reply delivery: {error}")
        return {"ran": True, "candidates": len(candidates), "processed": processed, "shadow_mode": self.config.shadow_mode}
