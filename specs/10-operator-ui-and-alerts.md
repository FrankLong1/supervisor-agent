# Plan 10: operator UI and alerts

## Outcome

Give the operator one truthful view of controllers, the unattended worker,
unread-feed freshness, deliveries, and attention items.

The current text/JSON status remains the source contract. A TUI or web view is
a consumer, never another controller or scheduler.

## Status model

Use named states before colors:

- `healthy`: identities verified, heartbeat and inventory fresh;
- `working`: a bounded decision or delivery is in progress;
- `attention`: action is stopped for a named safety or failure reason;
- `stopped`: intentionally or terminally stopped.

The UI may render `attention` as red, but must also show the exact reason,
affected host/task/receipt, first-seen time, and reviewed next action.

## Initial surfaces

```text
supervisor status --all --json
supervisor attention list
supervisor attention show ATTENTION_ID
```

Only after the JSON contract is stable should the loopback web console add:

- heartbeat and inventory freshness;
- delivery/clearance timeline;
- human-review queue;
- restart-budget exhaustion;
- canary expiry or identity mismatch; and
- links to reviewed CLI actions.

## Mutation rules

- Read views are default.
- Every write action maps to one controller command and is audited.
- The UI cannot clear an ambiguous delivery, forge canary evidence, restart
  past budget, or bypass the worker lease.
- Notifications deduplicate by attention identity and include recovery notices.

## Verification

- Text, JSON, and UI projections agree for the same state fixture.
- Every attention condition from plans 03-08 has a human-readable reason and
  remediation.
- UI mutation tests prove calls go through the validated controller interface.
- No UI process can acquire the scheduler or delivery lease.

## Done when

An operator can identify every red/attention condition and its safe next action
without inspecting SQLite, process tables, or raw logs.
