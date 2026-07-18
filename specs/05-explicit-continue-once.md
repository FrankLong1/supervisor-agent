# Plan 05: explicit continue-once

## Outcome

Allow an operator to process one selected unread task through the real
decision-and-delivery path exactly once.

This is the production proof before any recurring worker exists.

## User-visible contract

```text
supervisor continue-once THREAD_ID --receipt UPDATED_AT --host-id HOST
```

The receipt is required. The command never means "whatever is newest when you
get there." It acts only if the task is still idle, unread, and showing the
same receipt.

## Execution path

```text
re-read authoritative inventory
  -> validate current canary evidence
  -> atomically claim (host, thread, receipt)
  -> read bounded context
  -> decide CONTINUE or HUMAN_REVIEW
  -> if CONTINUE: re-check inventory + canary, then send once
  -> record acknowledgement
  -> confirm the claimed receipt clears
```

`HUMAN_REVIEW` creates a visible terminal attention item and sends nothing.
Context or classifier uncertainty does the same.

## Delivery state machine

```text
CLAIMED -> ACKNOWLEDGED -> CONFIRMED
    \-> ATTENTION
ACKNOWLEDGED -> ATTENTION on clearance timeout
```

Only `CLAIMED` may call transport, and only once. Startup reconciliation must
never turn an uncertain claim into permission to resend.

## Implementation slices

1. Make claims keyed by the exact unread receipt and enforce uniqueness in
   SQLite.
2. Add the one-task command with pre-send inventory and canary rechecks.
3. Route decision output to either one send or terminal attention.
4. Confirm clearance from a later authoritative inventory observation.
5. Add audit output showing every state transition and transport receipt.

## Verification

- Duplicate commands racing for one receipt produce one transport call.
- A changed receipt before send cancels the claim without delivering.
- Crash-before-acknowledgement and crash-after-acknowledgement both reconcile
  to attention, never retry.
- A real chosen task receives one continuation and the original receipt clears.

## Done when

The operator can safely continue one real unread task, and exhaustive failure
tests demonstrate that one receipt cannot produce more than one send attempt.
