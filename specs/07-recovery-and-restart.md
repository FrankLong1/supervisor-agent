# Plan 07: crash recovery and restart

## Outcome

Make controller and worker restarts boring before any service manager restarts
them automatically.

Recovery first decides what happened; it never treats process death as
permission to repeat an action.

## Recovery audit on startup

1. Verify stored controller/worker PID identity using PID plus process start
   ticks.
2. Acquire the single-instance lease.
3. Reconcile every nonterminal delivery claim against fresh authoritative
   inventory.
4. Convert uncertain `CLAIMED` or `ACKNOWLEDGED` records to attention unless
   clearance can be proved.
5. Resume polling only after reconciliation finishes.

## Operator restart

Implement explicit restart before automatic restart:

```text
supervisor restart SESSION_ID
supervisor worker-restart
```

Each command performs ordered stop, records the prior outcome, creates a new
runtime identity linked to the old one, and then starts. It cannot erase prior
claims or attention records.

## Bounded automatic restart contract

Automatic restart remains disabled until plan 08. The durable policy must be
ready first:

```text
restart_window_seconds
max_attempts_per_window
backoff schedule
last_attempt_at / reason / outcome
```

Never restart merely because a heartbeat is stale while identity or delivery
state is uncertain. That is attention.

## Verification

- Kill at every claim transition and prove restart never duplicates transport.
- PID-reuse tests reject stale identity.
- Restart budgets and backoff survive process restarts.
- Restart storms stop at the configured limit.
- Explicit stop is distinguishable from crash and is never auto-restarted.

## Done when

Repeated kill/restart fault tests preserve at-most-once delivery and every
uncertain transition ends in a durable, explainable attention state.
