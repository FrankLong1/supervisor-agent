# Supervisor implementation specs

The original unified-control-plane document described the destination. These
specs split that destination into small implementation contracts so the safe,
useful part can ship without pulling later infrastructure into the first pass.

## Build now

1. [Cleanup, unread detection, and one-shot dry run](01-cleanup-unread-dry-run.md)
   makes the task selector mean "unread" and provides one explicit safe test.
2. [Simple controller](02-simple-controller.md) makes one foreground process
   own the provider child, heartbeat, stop sequence, and small durable status
   record.

The first shipping checkpoint is:

```text
supervisor codex
supervisor scan-once
supervisor status --json
supervisor stop
```

The controller never scans tasks. The explicit `scan-once` diagnostic may
inspect genuinely unread Codex tasks and record what it would recommend, but
it cannot reply.

## Build later

1. [Live replies and the canary gate](later/10-live-replies-and-canary.md)
2. [Restart and recovery policy](later/11-restart-and-recovery.md)
3. [Detached terminals, services, and richer UI](later/12-detached-services-and-ui.md)

Later specs are not acceptance criteria for the first shipping checkpoint.

## Vocabulary

- **Controller:** the one process started by `supervisor codex` or
  `supervisor claude`. It owns the provider child and periodic work.
- **Dry run:** the manually invoked `scan-once` diagnostic. It may read an
  unread task, ask the decision provider what it recommends, and record that
  recommendation. It may not reply or change task-delivery state.
- **Inventory snapshot:** a saved schema-version-2 response from the Codex
  app's `list_threads` tool. It supplies client-owned unread state that the
  workstation app-server does not expose.
- **Live delivery:** later functionality that may reply to Codex. It does not
  exist in the first checkpoint and must eventually require the canary gate.
- **Canary:** a disposable test task used to prove that unread detection and
  one reply behave correctly. Its durable evidence acts as a safety key for
  later live delivery; it is irrelevant to the controller and dry run.
