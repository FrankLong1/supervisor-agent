# Later: live replies and the canary gate

## Why this is later

The one-shot dry run can prove selection and decision behavior without changing
a Codex task. Live delivery crosses that boundary by sending a reply, so it
needs separate evidence and a separate rollout.

## What a canary means

A canary is one disposable Codex task used as an end-to-end compatibility
test. It answers questions that unit tests cannot answer about the installed
Codex version and its real app-server:

- Does inventory reading identify the unread indicator without clearing it?
- Does one reply clear exactly that unread result?
- Does a later agent result create a new unread result?
- Does choosing human review leave the task visible and unread?

After a human verifies those facts, the supervisor records evidence tied to
the exact inventory-adapter identity and delivery-adapter identity. That
record is the canary gate. It is a safety key, not a background test performed
on production tasks.

## Live-mode requirements

Only this later work may add a live-delivery command or persistent mode.

Live startup is allowed only when all are true:

- the operator explicitly selects the later live-delivery command or flag;
- the provider is Codex;
- the inventory and delivery adapters publish stable identities;
- unrevoked canary evidence matches both identities; and
- the readiness check succeeds.

Repeat the canary match immediately before every delivery. A changed adapter,
missing evidence, revoked evidence, or unread ambiguity blocks the send.

Keep the existing delivery claim, transport acknowledgement, clearance
confirmation, and terminal human-review semantics. Never retry ambiguous
delivery.

## Canary procedure

1. Create a disposable task and produce one visible unread result.
2. Read inventory and confirm the unread indicator remains visible.
3. Run `supervisor scan-once` and confirm no reply or delivery claim is
   produced.
4. Under an explicit one-task canary command, send one reviewed reply.
5. Confirm the original unread result clears and is not selected again.
6. Produce a later agent result and confirm it has a new receipt.
7. Exercise a human-review result and confirm it remains visible.
8. Record evidence bound to the adapter identities.

## Acceptance criteria

- Live startup fails without matching canary evidence.
- A revoked or mismatched canary blocks the next send.
- One unread receipt can produce at most one delivery attempt.
- Ambiguous delivery and uncleared acknowledgement become terminal human
  review and are not retried.
