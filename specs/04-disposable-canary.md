# Plan 04: disposable canary command

## Outcome

Add one explicit command that proves the installed unread and reply adapters
work together on one disposable Codex task.

A canary is just a test task. It is not a runtime mode, a production cohort, or
a background process.

## User-visible contract

```text
supervisor canary THREAD_ID --host-id HOST
supervisor canary-status --json
supervisor canary-revoke EVIDENCE_ID
```

The command must display the target title, host, current receipt, exact test
message, and adapter identities before the one test send. It refuses non-idle,
non-unread, active, missing, or previously used targets.

## Procedure

1. The operator creates a disposable Codex task with an obvious test prompt.
2. The authoritative feed observes its unread result without clearing it.
3. The canary command claims that exact unread receipt.
4. It sends one unique, harmless continuation containing an evidence nonce.
5. It records the transport acknowledgement.
6. It confirms the original unread receipt clears.
7. It waits for the disposable task's next result and confirms a new receipt.
8. It records durable evidence bound to the inventory and delivery adapter
   identities.

If any step is ambiguous, evidence is not created and the task becomes an
attention item.

## Evidence record

```text
evidence_id
test_thread_id / host_id
old_receipt / new_receipt
inventory_identity / delivery_identity
transport_receipt
started_at / completed_at
operator / command version
revoked_at
```

Evidence is a compatibility proof for the exact adapters, not permission for a
classifier to bypass later delivery rules.

## Safety rules

- Only a disposable task may be used.
- Exactly one send is permitted per canary invocation.
- A second invocation against the same receipt is rejected.
- Changed adapter identities or revoked evidence fail readiness.
- Installation never creates evidence automatically.

## Verification

- Fake-adapter tests for success, no-clearance, duplicate invocation, changed
  identity, missing acknowledgement, timeout, and revoke.
- One real disposable-task run proves old-receipt clearance and new-receipt
  creation.
- Audit proves exactly one transport call.

## Done when

`canary-status` reports valid evidence for the currently installed inventory
and reply adapters, backed by one successful disposable-task run and zero
ambiguous sends.
