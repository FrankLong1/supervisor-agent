# Later: restart and recovery policy

## Goal

Add restart behavior only after controller shutdown is boring and well tested.

## First addition

Implement operator-requested restart before automatic restart:

```text
supervisor restart SESSION_ID
```

It must stop through the controller, preserve the previous session outcome,
and create a new session identity linked to the previous one.

## Automatic restart

The default remains disabled. A later bounded policy may restart only when:

- provider identity is known and the provider has definitely exited;
- no automation tick or delivery claim is in an uncertain state;
- the configured restart budget is not exhausted; and
- the reason and attempt are durably logged.

Never restart merely because a heartbeat is stale while a provider or live
delivery may still be running. That state requires attention.

## Acceptance criteria

- restart budgets survive controller restarts;
- bursts stop at the configured limit;
- every attempt has a recorded reason;
- live delivery uncertainty suppresses restart; and
- the operator can always select a no-restart policy.
