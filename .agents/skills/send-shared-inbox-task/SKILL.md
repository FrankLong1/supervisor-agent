---
name: send-shared-inbox-task
description: Queue an idempotent TASK_PROPOSAL in the shared Cloud SQL agent inbox for the other workstation owner. Use when the user asks to send, assign, hand off, delegate, or push a task between the actual Google users Alice (`alice@gravitationalventures.com`) and Frank (`frank@gravitationalventures.com`), or asks an agent on one workstation to leave work for the other agent.
---

# Send Shared Inbox Task

Use the bundled resolver; never type or infer a recipient address independently.

1. Read `references/directory.md` when owner, workstation, or agent names matter.
2. Require an explicit task subject and actionable body. Preserve constraints,
   expected result, relevant paths/links, and completion checks. Do not include
   credentials or tokens.
   Use the recipient's logical `workspace_key`; never send a filesystem path.
3. Read the fixed v0 directory in `references/directory.md`. Never recover
   retired synthetic identities from logs, prior output, or repository history.
4. Verify `SUPERVISOR_INBOX_AGENT_ADDRESS` and `SUPERVISOR_INBOX_AGENT_ID`
   jointly identify the sending workstation. Stop on a partial or ambiguous match.
5. Resolve a human request for Alice or Frank to the exact full Google email.
   Pass only that email to `scripts/send_task.py`; v0 accepts no other aliases.
6. Run from the repository root. Prefer `--body-file` for multiline tasks:

```bash
uv run python .agents/skills/send-shared-inbox-task/scripts/send_task.py \
  --to alice@gravitationalventures.com \
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
recipient policy evaluation. A trusted recipient may accept it into its durable
Codex queue and will respond with `TASK_ACCEPTED`; queuing alone is not acceptance.

The script automatically preserves `CODEX_THREAD_ID` when invoked from a Codex
task. This lets returned `RESULT` or `NEEDS_HUMAN` messages resume that exact
existing task. Do not remove or fabricate this correlation.

Never call raw PostgreSQL tables, change the recipient after a failed send, use
the sender's own address as recipient, or downgrade the message to NOTE to evade
the consent boundary.
