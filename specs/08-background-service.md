# Plan 08: background user service

## Outcome

Run the verified unattended worker after terminal close, logout, and reboot by
placing the existing worker under one user service manager.

This plan does not daemonize interactive Codex or Claude terminals.

## Service contract

```text
supervisor service-install --dry-run
supervisor service-install
supervisor service-enable
supervisor service-status
supervisor service-disable
supervisor service-uninstall
```

Installation renders reviewed units but never enables them. Enablement is a
separate explicit operator command.

## Ownership

- systemd owns process restart and backoff;
- the worker owns inventory, claims, delivery, and reconciliation;
- no watchdog sends signals directly to a provider or mutates delivery state;
- manual and service starts contend for the same worker lease.

Use `Restart=on-failure` only after plan 07 restart audits pass. Explicit stop,
policy failure, invalid canary, and exhausted restart budget must not form a
restart storm.

## Liveness

The service publishes:

```text
worker identity
heartbeat age
inventory age
last successful tick
last delivery confirmation
attention count and reasons
restart budget
```

A watchdog may report stale liveness, but it must not kill a process while a
claim is uncertain.

## Verification

- Units render deterministically and install disabled.
- Enable, logout-equivalent, stop, restart, and reboot-equivalent integration
  tests preserve one worker lease.
- Crash loops honor the durable restart budget.
- Service removal leaves durable audit state intact.

## Done when

The same foreground worker from plan 06 runs under the user service, survives
session loss, restarts only within policy, and never creates a second scheduler.
