# Architecture attack review

- **Target:** `README.md` architecture contract, validated against `src/codex_supervisor/` and `tests/test_supervisor.py`.
- **Intent:** Assess whether the fail-closed Codex unread-task supervisor design makes sense; no implementation change was requested.
- **Reviewer separation:** The required Claude-backed reviewer could not run because the local Claude CLI is not logged in. An isolated, attack-only reviewer was used instead; it made no file changes.
- **Check evidence:** `uv run --with pytest pytest -q` passed: 16 tests.

## Findings

### P0 — Successful delivery is neither durably claimed nor confirmed

- **Evidence:** `src/codex_supervisor/supervisor.py:31-45` reselects every currently unread candidate on each tick and stores no sent/pending state. `src/codex_supervisor/codex.py:41-50` treats a JSON-RPC return as delivery but does not verify a delivery receipt or the expected unread-state transition. The README makes one-result/one-reply consumption central to safety.
- **Failure mechanism:** A reply may be accepted before the native unread signal converges. A later tick can select the same unread result and start another reply; a nominal success creates no durable suppression record.
- **Why it matters:** It breaks the central safety property and can cause uncontrolled reply chains.
- **Confidence:** 0.93

### P0 — Read and classifier failures escape instead of producing a terminal marker

- **Evidence:** `src/codex_supervisor/supervisor.py:35` calls `read_context` and `claude.decide` outside an exception boundary; only `send_reply` is caught at lines 44-45. `src/codex_supervisor/claude.py:30-33` also lets `session.decide` exceptions propagate. The README promises uncertain context and missing session identity become `HUMAN_REVIEW_NEEDED`.
- **Failure mechanism:** Socket, decoding, or model-adapter failures abort a tick and leave the unread item eligible, so every scheduled run can repeat it indefinitely.
- **Why it matters:** This is fail-open operationally with respect to retry behavior, and it can block later candidates.
- **Confidence:** 0.96

### P1 — Canary and adapter trust are caller assertions, not enforceable production evidence

- **Evidence:** `src/codex_supervisor/scanner.py:8-30` treats inventory verification as a protocol and field-presence check. `src/codex_supervisor/models.py:57-68` enables mutation through caller-controlled booleans. `src/codex_supervisor/state.py` stores neither canary evidence nor an adapter/schema identity.
- **Failure mechanism:** An integration can supply an unreviewed inventory and set the three enablement booleans, bypassing the exact contract the README requires the canary to establish.
- **Why it matters:** The intended safety gate cannot detect drift in the inventory or delivery adapter.
- **Confidence:** 0.91

### P1 — The per-task model decision boundary is underspecified and cross-task state is shared

- **Evidence:** `src/codex_supervisor/supervisor.py:35-36` persists and reuses one session ID across candidates. `SYSTEM_PROMPT` in `src/codex_supervisor/claude.py:8` has no applied production adapter. `src/codex_supervisor/codex.py:36-39` limits context to the last two turns/five text items.
- **Failure mechanism:** A future adapter must decide how it applies the prompt and isolates session context. Without a per-task isolation contract, information from one task can influence later decisions, while truncation can remove relevant constraints.
- **Why it matters:** The claimed grounded, bounded decision contract is not demonstrably deployable beyond the fakes.
- **Confidence:** 0.90

## Contract assessment

The conceptual gates are coherent: verified unread state, terminal human-review markers, strict decision parsing, and explicit mutation enablement. Normal ticks are finite, but upstream exception retries and post-ack delivery ambiguity are not bounded. Human canary work is explicit, yet the code does not bind it to a specific adapter/version or preserve its evidence. Existing tests exercise the intended fake-path behavior but not the failure mechanisms above.
