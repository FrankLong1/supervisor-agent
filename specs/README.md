# Supervisor implementation specs

These specs are the ordered implementation plan. Each document adds one
observable capability and has its own tests and exit criteria. There is no
shadow/live mode matrix.

## What "unattended automation" actually means

It means one worker can keep doing this after the operator closes the terminal:

```text
read the real Codex unread list
  -> select tasks that are idle + unread
  -> claim one exact unread receipt
  -> read bounded context
  -> decide CONTINUE or HUMAN_REVIEW
  -> send at most one continuation
  -> confirm that exact unread receipt cleared
  -> sleep and repeat
```

If any fact is missing or a send is ambiguous, the worker stops acting on that
task and surfaces attention. It does not guess, retry blindly, or clear the
task.

This is not a separate AI personality and it is not "shadow mode." `scan-once`
is simply a manual, no-send test command. A canary is simply a disposable test
task. The recurring worker is added only after the same path works once under
an explicit command.

## Current checkpoint: complete

1. [Cleanup, unread detection, and one-shot dry run](01-cleanup-unread-dry-run.md)
2. [Simple foreground controller](02-simple-controller.md)

Available now:

```text
supervisor codex
supervisor claude
supervisor scan-once
supervisor status --json
supervisor stop
```

The controller manages one interactive provider process. `scan-once` can
inspect an app-provided unread snapshot and record a recommendation. Nothing
currently sends a Codex reply or runs a recurring unread scan.

## Ordered next chunks

| Order | Plan | New capability | Depends on |
| --- | --- | --- | --- |
| 3 | [Authoritative unread feed](03-authoritative-unread-feed.md) | Fresh unread state without manually saving snapshots | current checkpoint |
| 4 | [Disposable canary command](04-disposable-canary.md) | Prove one real test delivery against the installed Codex version | 3 |
| 5 | [Explicit continue-once](05-explicit-continue-once.md) | Continue one chosen real task, once | 4 |
| 6 | [Foreground unattended worker](06-unattended-worker.md) | Repeatedly process eligible tasks while one command stays running | 5 |
| 7 | [Crash recovery and restart](07-recovery-and-restart.md) | Resume safely after interruption without duplicate sends | 6 |
| 8 | [Background user service](08-background-service.md) | Keep the worker alive after logout/reboot | 7 |
| 9 | [Detached interactive sessions](09-detached-interactive-sessions.md) | Detach and reattach `supervisor codex`/`claude` | 2; independent of 3-8 |
| 10 | [Operator UI and alerts](10-operator-ui-and-alerts.md) | Show attention states and reviewed operator actions | 6-8 |

Plans 3-8 are the unattended automation path. Plan 9 is a convenience feature
for interactive sessions, not a prerequisite. Plan 10 observes the worker; it
must never become a second scheduler.

## Vocabulary

- **Controller:** the foreground process that owns one interactive Codex or
  Claude child.
- **Unread feed:** a fresh, read-only source of the Codex app's authoritative
  `hasUnreadTurn` value.
- **Unread receipt:** the `(host_id, thread_id, updated_at)` identity for one
  exact visible result.
- **Dry run:** one explicit analysis that records a recommendation and cannot
  send.
- **Canary:** a disposable Codex task used once to verify inventory and reply
  compatibility. It is a test, not a runtime mode.
- **Continue once:** one explicit, receipt-bound live action on a selected
  task.
- **Unattended worker:** one recurring foreground loop built from the verified
  continue-once operation.
- **Attention:** a fail-closed state requiring an operator. It is the concrete
  meaning behind a red status indicator.

## Invariants for every future chunk

- Never infer unread from idle status or transcript fingerprints.
- Never deliver twice for one unread receipt.
- Never retry an ambiguous delivery.
- Never let a UI, service manager, or classifier bypass delivery claims.
- Never enable a background service during package installation.
- Do not start the next chunk until the previous chunk's real acceptance test
  passes.
