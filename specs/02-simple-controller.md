# Simple controller

## Goal

Make `supervisor codex` and `supervisor claude` run one understandable
foreground controller that owns the provider child and lifecycle heartbeat.

Task analysis is not part of this loop. It happens only through the explicit
`supervisor scan-once` command.

## Process model

```text
operator terminal
  └─ supervisor controller
       └─ interactive Codex or Claude child
```

The provider uses the operator's terminal. Status can be viewed from another
terminal with `supervisor status`. Persisted state is durable; interactive I/O
is not detachable in this checkpoint.

The controller, not the provider child, is the managed process. `stop` signals
the controller so it can perform ordered provider shutdown.

## Controller loop

```text
preflight provider and workspace
persist session as starting
launch provider process group
persist session as running
while provider is alive and stop is not requested:
    publish lifecycle heartbeat
    poll provider exit without blocking signal handling
on natural provider exit:
    reap child and persist completed or failed
on stop:
    send SIGTERM to verified provider process group
    wait a bounded grace period
    use SIGKILL only if the grace period expires
    reap child and persist stopped
```

## Minimal session state

Persist only lifecycle facts:

```text
schema_version
id
provider / command / args
workspace
phase
controller_pid + controller_start_ticks
provider_pid + provider_start_ticks
created_at / started_at / ended_at
heartbeat_at
stop_requested_at
reason
```

Use atomic file replacement. Keep unread-analysis SQLite state separate and
authoritative for its audit and review data. Lifecycle logs remain simple
append-only files.

## Commands

```text
supervisor codex [--task TEXT]
supervisor claude [--task TEXT]
supervisor scan-once
supervisor status [SESSION_ID] [--watch] [--json]
supervisor stop [SESSION_ID] [--force]
supervisor logs [SESSION_ID] [--follow]
supervisor doctor
```

Do not implement automatic restart, detach/reattach, or a recurring task
scheduler in this checkpoint.

## Status

Text and JSON expose the same facts:

```text
Session:    <id>  codex  running
Provider:   pid <pid>, identity verified
Controller: pid <pid>, identity verified
Heartbeat:  1s ago
Health:     healthy
Actions:    supervisor logs <id> | supervisor stop <id>
```

Health is derived from process identities and heartbeat freshness rather than
persisted as a second source of truth.

## Tests

Use fake provider commands to prove:

- controller and provider identity persistence;
- heartbeat refresh;
- natural zero and nonzero provider exit handling;
- controller-first external stop signaling;
- bounded provider shutdown and child reaping;
- stale identity becoming attention;
- text/JSON status projection; and
- `logs --follow` ending when the controller ends.

## Acceptance criteria

- `supervisor codex` runs until Codex exits or the operator stops it.
- `supervisor stop` reaches a live controller instead of bypassing it.
- Provider shutdown is ordered and the child is reaped.
- The provider is never silently restarted.
- A stale controller or provider identity is reported as attention.
- All unread dry-run safety tests remain green.

