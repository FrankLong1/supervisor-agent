from __future__ import annotations

import fcntl
from typing import Protocol

from .claude import ConservativeClaude
from .models import DecisionKind, SupervisorConfig
from .scanner import UnreadScanner
from .state import SupervisorState


class ContextReader(Protocol):
    def read_context(self, candidate): ...
    def send_reply(self, candidate, reply: str): ...


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
        observed_unread = {(candidate.host_id, candidate.thread_id, candidate.unread_at) for candidate in scanned.candidates if candidate.unread_at is not None}
        self.state.reconcile_delivery_claims(observed_unread, self.config.delivery_confirmation_timeout_seconds)
        candidates = [item for item in scanned.candidates if not self.state.is_human_review(item.host_id, item.thread_id)]
        if not candidates: return {"ran": True, "candidates": 0, "reason": "no eligible idle threads"}
        processed = 0
        for candidate in candidates:
            if candidate.unread_at is None:
                self.state.mark_human_review(candidate.host_id, candidate.thread_id, "unread task has no stable receipt key")
                continue
            claim = self.state.delivery_claim(candidate.host_id, candidate.thread_id, candidate.unread_at)
            if claim is not None and claim[0] != "CONFIRMED":
                continue
            try:
                context = self.codex.read_context(candidate)
                candidate = context.candidate
                if not context.recent_messages or context.latest_visible_result is None:
                    raise ValueError("bounded task context is empty or has no visible result")
                returned_id, decision = self.claude.decide(self.state.session_id(), context)
                if returned_id != "invalid-session": self.state.set_session_id(returned_id)
            except Exception as error:
                self.state.mark_human_review(candidate.host_id, candidate.thread_id, f"context or classifier failure: {error}")
                continue
            processed += 1
            if self.config.shadow_mode:
                self.state.record_shadow(candidate.host_id, candidate.thread_id, decision.kind.value, decision.reason, decision.reply); continue
            if decision.kind is DecisionKind.HUMAN_REVIEW_NEEDED:
                self.state.mark_human_review(candidate.host_id, candidate.thread_id, decision.reason); continue
            if not self.config.mutation_requested():
                self.state.mark_human_review(candidate.host_id, candidate.thread_id, "reply blocked: explicit delivery enablement is required"); continue
            if not self.state.claim_delivery(candidate.host_id, candidate.thread_id, candidate.unread_at):
                continue
            try:
                receipt = self.codex.send_reply(candidate, decision.reply or "")
                if not getattr(receipt, "transport_accepted", False):
                    raise RuntimeError("delivery adapter returned no transport acknowledgement")
            except Exception as error: self.state.mark_human_review(candidate.host_id, candidate.thread_id, f"ambiguous reply delivery: {error}")
            else: self.state.await_clearance(candidate.host_id, candidate.thread_id, candidate.unread_at)
        return {"ran": True, "candidates": len(candidates), "processed": processed, "shadow_mode": self.config.shadow_mode}
