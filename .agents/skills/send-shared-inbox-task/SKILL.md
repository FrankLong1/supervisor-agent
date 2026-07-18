---
name: send-shared-inbox-task
description: Queue an idempotent TASK_PROPOSAL in the shared Cloud SQL agent inbox for the other workstation owner. Use when the user asks to send, assign, hand off, delegate, or push a task between Alice/alice@ (`research@alice`) and Frank/frank@/Bob (`helper@bob`), or asks an agent on one workstation to leave work for the other agent.
---

# Send Shared Inbox Task

Use the bundled resolver; never type or infer a recipient address independently.

1. Read `references/directory.md` when owner, workstation, or agent names matter.
2. Require an explicit task subject and actionable body. Preserve constraints,
   expected result, relevant paths/links, and completion checks. Do not include
   credentials or tokens.
3. Verify `SUPERVISOR_INBOX_AGENT_ADDRESS` identifies the sending workstation.
   Stop if it is absent or not exactly `research@alice` or `helper@bob`.
4. Resolve `--to` through `scripts/send_task.py`. Accept `alice`, `alice@`,
   `frank`, `frank@`, or `bob`; the script rejects unknown and self recipients.
5. Run from the repository root. Prefer `--body-file` for multiline tasks:

```bash
uv run python .agents/skills/send-shared-inbox-task/scripts/send_task.py \
  --to alice \
  --subject "Investigate the failed import" \
  --body-file /tmp/shared-inbox-task.txt
```

Use `--dry-run` first only when the requested recipient is ambiguous or the
operator asks for a preview. An explicit request to send or delegate authorizes
the queue mutation; do not add a redundant confirmation.

Report the returned sender address, immutable message/delivery/thread IDs,
recipient address, and whether `created` is true. `created: false` is a safe
idempotent retry, not failure.

Call the result **queued**, never accepted or started. `TASK_PROPOSAL` requires
recipient review by policy. The recipient workstation must run
`supervisor inbox scan-once` and then its reviewed pickup workflow; the current
checkpoint does not have an always-on polling daemon or automatic task acceptance.

Never call raw PostgreSQL tables, change the recipient after a failed send, use
the sender's own address as recipient, or downgrade the message to NOTE to evade
the consent boundary.
