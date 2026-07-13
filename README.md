# Codex unread-task supervisor

This is a small, local Python supervisor for Codex tasks that have a verified unread/new-result signal. It is deliberately fail-closed: it performs no automatic reply unless an operator has separately proven that the inventory's `hasUnreadTurn` precisely matches Codex Mac's blue-dot state and native delivery consumes exactly that result.

## Architecture

`UnreadScanner` is the first deterministic gate. It accepts only a **verified, read-only** inventory that provides `hasUnreadTurn` for every unarchived task, excludes the supervisor's own task, and returns unread tasks oldest first. `SupervisorState` is the second gate: terminal `HUMAN_REVIEW_NEEDED` markers in SQLite always win. Only then does `Supervisor` read a bounded context, resume one injected Claude/Fable session ID, and validate one decision.

The allowed decision JSON has exactly these fields:

```json
{"decision":"REPLY", "reason":"short grounded explanation", "reply":"concrete next instruction"}
```

or:

```json
{"decision":"HUMAN_REVIEW_NEEDED", "reason":"short grounded explanation", "reply":null}
```

Invalid output, uncertain context, completion, missing session identity, blocked delivery, and ambiguous delivery all become `HUMAN_REVIEW_NEEDED`. The app-server adapter talks newline-delimited JSON-RPC over its Unix socket. It uses `turn/steer` only when an active turn ID exists; idle delivery follows `thread/resume` then `turn/start`.

## Safe setup and state

Install in an isolated environment, then inspect the default-safe commands:

```bash
uv run --with pytest pytest -q
uv run codex-unread-supervisor status
uv run codex-unread-supervisor canary-readiness
```

State is outside the checkout at `$XDG_STATE_HOME/demo-agent-supervisor/state.sqlite3`, defaulting to `~/.local/state/demo-agent-supervisor/state.sqlite3`. It contains only terminal human-review markers, the one persisted supervisor session ID, and shadow-decision audit records.

`scan-once` intentionally exits without running: this project does not include a guessed Claude/Fable CLI/session-resume adapter, and the currently observed app-server inventory does not expose `hasUnreadTurn`. Provide adapters only after their actual contracts are reviewed and tested. No cron configuration is included.

## Shadow mode and enablement

The library's default config is shadow mode. In that mode it evaluates injected fakes/reviewed adapters and records proposed decisions but neither replies nor writes terminal markers. A non-shadow run still refuses delivery unless all three are explicit: `shadow_mode=False`, `allow_replies=True`, and `canary_verified=True`.

Before any general automation, use one disposable Codex task and record evidence that:

1. The verified inventory scan leaves its blue dot/unread state visible in Codex Mac.
2. A successful native reply consumes exactly that unread result.
3. The source agent's next visible result creates a new unread signal.
4. An intentionally routed `HUMAN_REVIEW_NEEDED` task keeps its blue dot.

Only after that evidence and a reviewed production Claude/Fable resume adapter may an operator create an explicitly configured scheduler. If either condition changes, disable replies again. Never replace unread semantics with fingerprints.

## Operator actions

View durable state:

```bash
uv run codex-unread-supervisor status
```

An operator can explicitly re-enroll a task after resolving it:

```bash
uv run codex-unread-supervisor reset-human-review --host-id local --thread-id THREAD_ID
```

That is the only supported way to clear a terminal marker. No agent or classifier can clear it.

## Failure behavior

Missing unread capability, an unverified inventory, and no eligible task stop before Claude is invoked. The nonblocking lock prevents overlapping ticks. Ambiguous send failures are never retried: the task is marked for human review. `HUMAN_REVIEW_NEEDED` does not acknowledge the unread result, so the blue dot remains for the operator.
