# Codex unread-task supervisor

This is a small, local Python supervisor for Codex tasks. It scans every app-server thread whose runtime status is `idle`; Fable either continues the task or creates a permanent `HUMAN_REVIEW_NEEDED` stop marker.

## Architecture

`UnreadScanner` is the first deterministic gate. It accepts only a **read-only** inventory that provides runtime status for every unarchived task, retains only `idle` threads, excludes the supervisor's own task, and orders candidates by `updatedAt`. `SupervisorState` is the second gate: terminal `HUMAN_REVIEW_NEEDED` markers in SQLite always win.

Before a reply is sent, SQLite atomically records a claim for that exact `(host, task, unreadAt)` result. A successful transport acknowledgement moves the claim to `AWAITING_CLEARANCE`; no later tick can send that result again. The claim is confirmed only when the inventory no longer reports that same receipt. A claim interrupted before acknowledgement, or one whose unread signal does not clear within the configured confirmation timeout, becomes terminal human review. This favors a visible blue dot over a duplicate reply.

Only then does `Supervisor` read a bounded context, resume an injected Claude/Fable session **for that one task**, apply the required supervisor prompt, and validate one decision. Read-context and classifier failures also become terminal human review; they are never silently retried.

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

State is outside the checkout at `$XDG_STATE_HOME/demo-agent-supervisor/state.sqlite3`, defaulting to `~/.local/state/demo-agent-supervisor/state.sqlite3`. It contains terminal human-review markers, per-task supervisor session IDs, shadow-decision audit records, delivery claims, and canary evidence bindings.

`scan-once` runs one shadow-only cycle. The local Fable CLI's model, session creation/resume, and tools-disabled invocation were verified before it was wired. It never replies to Codex in this mode.

## Boot and liveness (Linux)

The package includes a **disabled-by-default** `systemd --user` service and watchdog timer. The managed `serve` process scans idle threads every 30 seconds, invokes Fable, and emits a heartbeat.

Preview the files first:

```bash
uv run codex-unread-supervisor service-install --dry-run
```

Install them (still disabled), inspect the generated units, then explicitly enable only a shadow-mode deployment:

```bash
uv run codex-unread-supervisor service-install
systemctl --user enable --now codex-unread-supervisor.service codex-unread-supervisor-watchdog.timer
uv run codex-unread-supervisor doctor --strict
```

`doctor --json` reports independent state, Codex-socket, and heartbeat checks. `watchdog` is the strict, one-shot form used by the timer. A stale heartbeat is reported only—this release never kills or restarts an active worker, because that could make a delivery ambiguous. Remove the integration with:

```bash
uv run codex-unread-supervisor service-uninstall
```

## Shadow mode and enablement

The library's default config is shadow mode. In that mode it evaluates injected fakes/reviewed adapters and records proposed decisions but neither replies nor writes terminal markers. A non-shadow run still refuses delivery unless explicit enablement is paired with a durable canary-evidence record whose inventory and delivery-adapter identities exactly match the running configuration. A boolean is not canary evidence.

Before enabling any Codex reply, use one disposable Codex task and record evidence that:

1. `waitingOnUserInput` identifies the intended human-input state without changing it.
2. A successful native reply transitions that exact task out of the waiting state.
3. A later source-agent request for input creates a new waiting state.
4. An intentionally routed `HUMAN_REVIEW_NEEDED` task remains visible to the operator.

Only after that evidence has been recorded against the exact inventory and delivery-adapter versions, and after a reviewed production Claude/Fable resume adapter defines task isolation and prompt application, may an operator create an explicitly configured scheduler. If either adapter identity changes, the stored evidence no longer matches and replies are refused. Never replace unread semantics with fingerprints.

## Operator actions

View durable state:

```bash
uv run codex-unread-supervisor status
```

Render the read-only Human review queue, which is the same row shape intended
for the later local console. It preserves the title snapshot captured when a
marker is created; older markers without a snapshot display as `Untitled task`.

```bash
uv run codex-unread-supervisor human-review-queue
```

Each row includes the Codex task ID. Codex does not publish a supported desktop
deep-link scheme, so use the task title/ID to locate it in the app.

Open the same queue in a local browser:

```bash
./scripts/open-backlog.sh
```

It binds only to `http://127.0.0.1:8765/`, refreshes every 15 seconds, and is read-only.

An operator can explicitly re-enroll a task after resolving it:

```bash
uv run codex-unread-supervisor reset-human-review --host-id local --thread-id THREAD_ID
```

That is the only supported way to clear a terminal marker. No agent or classifier can clear it.

## Failure behavior

Missing status capability, a missing receipt key, and no eligible task stop before Fable is invoked. The nonblocking lock prevents overlapping ticks. Context and classifier failures, ambiguous sends, interrupted delivery claims, and uncleared acknowledged delivery are never retried: the task is marked for human review. `HUMAN_REVIEW_NEEDED` leaves the thread visible for the operator.
