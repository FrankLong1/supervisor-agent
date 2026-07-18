# Unified Supervisor Control Plane

This plan has been split into implementable specs. The old all-at-once handoff
mixed immediate safety cleanup with live delivery, restart policy, terminal
multiplexing, services, and dashboard work. Git history retains that document
if its architectural discussion is needed.

## Implement now

1. [Cleanup, unread detection, and one-shot dry run](../specs/01-cleanup-unread-dry-run.md)
2. [Simple controller](../specs/02-simple-controller.md)

The first checkpoint manages one foreground provider process, detects only
genuinely unread Codex tasks only when `scan-once` is invoked, and records
recommendations without any automatic reply path.

## Implement later

1. [Live replies and the canary gate](../specs/later/10-live-replies-and-canary.md)
2. [Restart and recovery policy](../specs/later/11-restart-and-recovery.md)
3. [Detached terminals, services, and richer UI](../specs/later/12-detached-services-and-ui.md)

See the [specs index](../specs/README.md) for vocabulary, order, and the first
shipping command contract.
