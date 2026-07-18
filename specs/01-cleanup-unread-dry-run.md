# Cleanup, unread detection, and one-shot dry run

## Goal

Provide one explicit command that tests the unread-task analysis against real
Codex state without sending or scheduling anything:

```text
supervisor scan-once
```

This is a diagnostic command, not a persistent product mode.

## Inventory source

The workstation app-server's `thread/list` response does not expose Codex
client unread state. The Codex app's `list_threads` tool does expose
`hasUnreadTurn` across local and remote hosts.

Therefore the command supports two explicit outcomes:

1. With `--inventory-snapshot PATH`, adapt a saved Codex app schema-version-2
   `list_threads` response, filter it to `--host-id`, and analyze it.
2. Without an inventory that exposes unread state, return
   `unread_supported: false`, select nothing, and exit nonzero.

Never infer unread state from `idle`, `updatedAt`, transcript fingerprints, or
the existence of a completed turn.

## Exact unread selection

A task is eligible only when all of the following are true:

- it is unarchived;
- `status.type` is exactly `idle`;
- `hasUnreadTurn` is exactly `true`;
- `updatedAt` is a stable integer receipt;
- it is not the supervisor's own task; and
- it has no existing terminal human-review marker.

If an idle task omits `hasUnreadTurn`, reports a non-boolean value, or lacks a
stable receipt while unread, the whole scan fails closed. It returns no
candidates and does not invoke the decision provider.

Inventory reading must not acknowledge or clear the unread result.

## Dry-run behavior

For every eligible task, the command:

1. reads bounded context;
2. asks the constrained decision provider for a proposed reply or review
   recommendation;
3. records that recommendation in supervisor-owned audit state; and
4. returns a structured summary.

Context failures, classifier failures, and invalid classifier output are
recorded as dry-run review recommendations.

The dry run may persist its audit records and decision-provider session ID. It
must not:

- call the Codex reply method;
- create or update delivery claims;
- clear unread state;
- create terminal human-review markers; or
- otherwise mutate a Codex task.

## Command wiring

- `supervisor scan-once` delegates to this dry-run workflow.
- `--inventory-snapshot` accepts the authoritative Codex app thread snapshot
  when a Codex-run diagnostic supplies one.
- `codex-unread-supervisor scan-once` exposes the same workflow for
  compatibility.
- `codex-unread-supervisor serve` is heartbeat-only and does not scan tasks.
- No current command accepts or enables a live-delivery mode.

## Tests

Prove:

1. idle + unread is selected;
2. idle + not unread is skipped;
3. active + unread is skipped;
4. missing or malformed unread state fails the tick closed;
5. a proposed reply is audited and `send_reply` is never called;
6. errors and review recommendations do not create terminal markers;
7. no delivery claim is created;
8. the one-shot command is wired only to the dry-run builder; and
9. single-flight locking, bounded context, decision-session reuse, and output
   validation continue to work.

## Acceptance criteria

- No current CLI command reaches automatic reply delivery.
- A fake Codex client that raises from `send_reply()` completes all dry-run
  tests without raising.
- A real Codex app inventory snapshot selects only unread tasks for the chosen
  host, and the post-run audit contains zero delivery claims and zero new
  terminal markers.
