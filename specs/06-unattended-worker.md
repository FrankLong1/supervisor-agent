# Plan 06: foreground unattended worker

## Outcome

Turn the verified `continue-once` operation into one recurring foreground
worker. This is the actual unattended automation loop.

It is introduced as a foreground command first so its behavior is visible and
stopping it is ordinary process control. Background services come later.

## User-visible contract

```text
supervisor watch-unread --host-id HOST --policy PATH
supervisor worker-status --json
supervisor worker-stop
```

This is a distinct command with a distinct behavior, not a "live mode" flag on
the interactive controller.

## One loop

```text
while not stopping:
    require a fresh authoritative inventory
    reconcile existing claims before selecting new work
    select idle + unread receipts in deterministic order
    for each candidate within the tick budget:
        execute the continue-once state machine
    publish heartbeat and counters
    wait interruptibly for the next interval
```

There is one scheduler and one delivery authority. The UI, systemd, and the
interactive `supervisor codex` controller never run a second copy of this loop.

## Initial policy

Keep policy deliberately small and file-backed:

```text
allowed_host_ids
allowed_workspace_roots
poll_interval_seconds
max_candidates_per_tick
max_sends_per_hour
inventory_max_age_seconds
clearance_timeout_seconds
```

Default-deny missing hosts/workspaces. Do not add opaque confidence scores or
per-task mode switches.

## Attention conditions

The worker stays alive but stops acting on the affected scope when it sees:

- stale or missing unread inventory;
- mismatched/revoked canary evidence;
- ambiguous delivery or clearance timeout;
- classifier/context failure for a task;
- exhausted send budget; or
- another live worker holding the single-instance lease.

These are the concrete red conditions. Status must name the reason and the
operator action; a red color alone is insufficient.

## Verification

- Fake-clock tests for polling, budgets, interruptible shutdown, and heartbeat.
- Multi-process test proving only one worker acquires the lease.
- A mixed inventory proves deterministic selection and per-task isolation.
- Injected failures prove the worker skips/halts the correct scope without
  duplicate sends.
- A bounded real trial processes only explicitly allowlisted disposable tasks.

## Done when

One foreground worker can run for a bounded trial, process multiple new unread
receipts once each, expose every attention reason, and shut down without an
uncertain claim.
