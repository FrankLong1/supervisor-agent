# Supervisor shared inbox operator guide

The shared inbox is an optional, fail-closed PostgreSQL adapter. It does not
change the existing Codex unread-task scanner, delivery claims, canary, or
human-review behavior. It has no polling daemon and never invokes a generic
LLM/provider.

Install the optional client dependency with `pip install -e '.[inbox]'` and
point the DSN at a Cloud SQL Auth Proxy port or socket. Do not put credentials
in this repository or in the SQLite state database.

```sh
export SUPERVISOR_INBOX_MODE=dry-run
export SUPERVISOR_INBOX_DSN='postgresql://...'
export SUPERVISOR_INBOX_INSTANCE_ID='stable-workstation-instance'
export SUPERVISOR_INBOX_AGENT_ID='00000000-0000-0000-0000-000000000000'
```

`SUPERVISOR_INBOX_SCHEMA` defaults to `public`. Poll seconds must be 5–300 and
claim seconds 5–900. The inbox defaults to `disabled`; merely running existing
`supervisor codex`, `supervisor claude`, or `supervisor scan-once` commands does
not connect to PostgreSQL.

Use `supervisor inbox status` for local status. It masks the connection target
and does not connect unless `--check-connection` is supplied. Use
`supervisor inbox scan-once` for a read-only stored-function listing and local,
body-free routing observations.

## Synthetic canary and one-shot handling

Live commands require `SUPERVISOR_INBOX_MODE=one-shot` and an immutable agent
UUID. The synthetic canary must be the single oldest queued delivery, be a
`QUESTION`, and contain `{"supervisor_canary": true}` in `body_json`.

```sh
supervisor inbox canary --delivery-id DELIVERY_UUID
```

The first invocation claims exactly that expected delivery, sends a bounded
non-LLM `RESULT`, completes it, and prints the reply message UUID. It does not
enroll canary evidence yet. Verify through the sender's separately
authenticated smoke harness that exactly one reply exists in the original
thread, then bind that observation:

```sh
supervisor inbox canary --delivery-id DELIVERY_UUID \
  --sender-observed-reply-id REPLY_MESSAGE_UUID
```

Only matching contract, adapter, database session identity, local instance,
and handler evidence enables `supervisor inbox run-once`. Run-once claims at
most one delivery and only uses deterministic routing. A `TASK_PROPOSAL` always
goes to human review. Any uncertain acknowledgement, send, completion,
recipient, or delivery identity becomes terminal `AMBIGUOUS` local state and
is never retried automatically.

The PostgreSQL client calls only `inbox_list_deliveries`,
`inbox_claim_next_delivery`, `inbox_mark_received`,
`inbox_complete_delivery`, and `inbox_send_message` in the configured schema.
It performs no raw mailbox table DML.

## Sending a task between workstations

Use the repository skill at
`.agents/skills/send-shared-inbox-task/SKILL.md`. The two runtime principals are
the actual Google users `alice@gravitationalventures.com` and
`frank@gravitationalventures.com`; the retired synthetic Alice/Bob service
accounts are not valid deployment identities. Agent addresses and UUIDs remain
separate deployment outputs. Configure the four directory variables documented
in the skill reference. Its resolver rejects incomplete, unknown, duplicate, or
self mappings and submits only an idempotent `TASK_PROPOSAL` through
`inbox_send_message`.

```bash
uv run python .agents/skills/send-shared-inbox-task/scripts/send_task.py \
  --to alice --subject "Investigate the import" --body-file /tmp/task.txt
```

A successful result means queued, not accepted. The recipient's supervisor
routes every `TASK_PROPOSAL` to `NEEDS_HUMAN` until an explicit acceptance
workflow is implemented.
