# Inbox-driven Codex task execution plan

## Status

Proposed next implementation track. This plan extends the completed Cloud SQL
inbox adapter; it does not weaken the existing database identity, claim,
canary, or idempotency boundaries.

The Codex app-server contract was checked against the locally installed Codex
schema and the current Codex manual on 2026-07-18. The stable lifecycle needed
here is:

```text
thread/start  -> create a Codex conversation
turn/start    -> submit accepted work
thread/resume -> continue an idle conversation
thread/read   -> reconcile and collect results
```

App-server is an experimental integration surface whose generated schema is
version-specific. Capture and test the installed schema in CI rather than
assuming fields that are not present in the running version.

## Objective

Allow a trusted `TASK_PROPOSAL` from the shared Cloud SQL inbox to become a new
local Codex app task, allow later inbox messages to continue an existing mapped
Codex task, and return progress or results to the originating supervisor.

Two supervisors may coordinate indefinitely. The design prevents duplicate
work and uncontrolled fan-out; it does not impose a hop-count or conversation
TTL that would deliberately stop useful supervisor-to-supervisor work.

```text
Supervisor A / Codex task
  -> TASK_PROPOSAL
  -> Cloud SQL thread
  -> Supervisor B accepts into a durable local job
  -> Codex thread/start + turn/start
  -> TASK_ACCEPTED / PROGRESS / RESULT
  -> Supervisor A resumes the mapped existing Codex task
  -> that task may send another TASK_PROPOSAL
```

## Current boundary to change

Today:

- every `TASK_PROPOSAL` routes to `HUMAN_REVIEW` even when the contact grant
  permits unattended execution;
- inbox handling has no dependency on `AppServerClient`;
- `AppServerClient` can list, read, resume, steer, and start turns in existing
  threads but has no typed method for creating an inbox-owned thread;
- the cockpit is one fixed existing thread used only for body-free status;
- the shared-task skill creates a Cloud SQL proposal but records no originating
  Codex thread; and
- local SQLite has inbox receipt state but no durable inbox-to-Codex run ledger.

Preserve the cockpit as a status surface. Do not turn it into the executor or
use it as a hidden substitute for `thread/start`.

## Required behavior

### 1. Explicit recipient execution policy

Automatic acceptance requires all of the following:

1. `SUPERVISOR_INBOX_MODE=poll` and the existing sender-verified inbox canary
   match.
2. The Cloud SQL contact grant returns
   `allow_unattended_execution=true` for the authenticated sender/recipient
   principal pair.
3. A separate local setting enables execution, for example
   `SUPERVISOR_INBOX_EXECUTION_MODE=review|trusted`, defaulting to `review`.
4. The message is `TASK_PROPOSAL` with a valid `shared-inbox-task/v1` body.
5. Its `workspace_key` resolves through a recipient-owned local allowlist.
6. The delivery has no existing local job or already-mapped Codex thread.

Anything else remains `NEEDS_HUMAN`; malformed payloads remain
`NOT_UNDERSTOOD`. A sender cannot select an absolute local path, model,
sandbox, approval policy, developer instruction, credential, or executable.

Use a recipient-owned configuration file rather than a large environment
variable for workspace mappings, for example:

```json
{
  "version": 1,
  "workspaces": {
    "supervisor-agent": "/home/user/supervisor-agent"
  },
  "default_workspace": "supervisor-agent",
  "max_active_runs": 2,
  "max_active_runs_per_workspace": 1
}
```

Validate canonical absolute paths at startup and require each target to be an
existing trusted workspace. Capacity controls dispatch, not acceptance: an
accepted task can wait durably in a local queue without blocking the Cloud SQL
inbox or being declined merely because another Codex task is active.

### 2. Versioned task envelope

Extend `send-task` and the bundled skill to emit:

```json
{
  "format": "shared-inbox-task/v1",
  "workspace_key": "supervisor-agent",
  "sender_agent_address": "alice@gravitationalventures.com",
  "source_codex_thread_id": "optional UUID",
  "expected_result": "optional bounded text"
}
```

The actionable request remains in `body_text`. Treat all sender text as
untrusted user content and wrap it in a locally generated provenance block
before giving it to Codex. Continue accepting the current v0 format only into
human review; do not silently infer a workspace or autonomous authority.

`source_codex_thread_id` is correlation metadata for the sending supervisor.
The recipient must not use it as a local app-server thread ID. The sender stores
its own outbound Cloud SQL thread/message mapping to that local Codex thread.

### 3. Durable local execution ledger

Add a dedicated table rather than overloading
`supervisor_inbox_processing`:

```text
supervisor_inbox_runs
  delivery_id primary key
  message_id
  inbox_thread_id
  sender_agent_id
  sender_address
  workspace_key
  workspace_path
  task_body              temporary execution payload
  task_body_sha256
  status
  codex_thread_id unique null
  codex_turn_id null
  client_user_message_id unique
  accepted_message_id null
  result_message_id null
  last_codex_status null
  last_error_type null
  created_at
  updated_at
  finished_at null
```

Statuses:

```text
ACCEPTED_QUEUED
CREATE_REQUESTED
THREAD_CREATED
TURN_START_REQUESTED
RUNNING
WAITING
SUCCEEDED
FAILED
NEEDS_HUMAN
AMBIGUOUS
```

Also add:

```text
supervisor_inbox_outbound_correlations
  inbox_thread_id primary key
  proposal_message_id unique
  proposal_delivery_id unique
  source_codex_thread_id null
  recipient_address
  created_at

supervisor_inbox_session_deliveries
  delivery_id primary key
  inbox_thread_id
  target_codex_thread_id
  client_user_message_id unique
  status
  codex_turn_id null
  last_error_type null
  created_at
  updated_at
```

The task body is execution state, not audit state. Keep the SQLite file private,
never include the body in heartbeat/cockpit/log output, and clear `task_body`
after `turn/start` is durably reconciled. Retain only bounded metadata and its
hash after terminal completion.

Every state transition must be committed before its corresponding external
side effect. Unknown outcomes become `AMBIGUOUS`; they never cause a blind
second `thread/start`.

### 4. Acceptance and dispatch state machine

Change routing to add an `ACCEPT_TASK` route only when the full local and shared
policy passes. The handler then:

1. Claims and marks the Cloud SQL delivery received using the existing guarded
   path.
2. Inserts `ACCEPTED_QUEUED` keyed by `delivery_id`, including the canonical
   prompt and deterministic `client_user_message_id`.
3. Sends `TASK_ACCEPTED` on the same Cloud SQL thread with a stable
   idempotency key and a bounded status of `queued`.
4. Completes the inbound delivery only after both the local job and accepted
   reply IDs are durable.
5. Lets the local dispatcher select queued jobs under the configured global
   and per-workspace capacity.
6. Commits `CREATE_REQUESTED`, calls `thread/start` with the locally resolved
   `cwd` and recipient-owned execution defaults, then immediately records the
   returned Codex thread ID.
7. Commits `TURN_START_REQUESTED`, calls `turn/start` with the new thread ID,
   deterministic `clientUserMessageId`, and provenance-wrapped task input, then
   records the returned turn ID and `RUNNING`.
8. Clears the temporary task body after a successful read-back proves that the
   expected turn belongs to the mapped thread.

Cloud SQL idempotency keys should be derived from the delivery ID and operation:

```text
task-accepted:<sha256(handler:delivery_id)>
task-progress:<sha256(handler:delivery_id:progress_edge)>
task-result:<sha256(handler:delivery_id:terminal_state)>
```

#### App-server ambiguity rule

`thread/start` has no documented client idempotency key. Therefore:

- if the request fails before bytes are sent, return the job to
  `ACCEPTED_QUEUED` with bounded retry backoff;
- if a response containing a thread ID is received, persist it immediately;
- if the connection fails after the request may have reached app-server but
  before a thread ID is known, mark `AMBIGUOUS` and surface human review;
- never issue a second `thread/start` for that delivery automatically.

For `turn/start`, first test the installed app-server's actual
`clientUserMessageId` replay behavior. If it is not an explicit idempotency
guarantee, reconcile with `thread/read`; retry only when absence is provable.
Otherwise mark `AMBIGUOUS` rather than risking duplicate turns.

This is the unavoidable distributed-systems boundary: SQLite, Cloud SQL, and
app-server do not share a transaction. The correct failure mode is a visible
orphan or paused job, not duplicate autonomous work.

### 5. Monitor owned Codex tasks

Do not depend on the unavailable `hasUnreadTurn` signal for inbox-owned work.
The supervisor already has exact Codex thread IDs in its local run ledger.

On each worker tick, within a bounded batch:

1. Read active mapped threads with `thread/read(includeTurns=true)`.
2. Reconcile the recorded turn ID and current turn status.
3. When a task remains active, update local status only; send `PROGRESS` only on
   meaningful state edges or a coarse heartbeat interval, never every poll.
4. When the turn completes, extract the bounded final agent message.
5. Send `RESULT` on the original Cloud SQL thread using a deterministic key.
6. When the turn fails, is interrupted, lacks a usable final response, or needs
   an approval the supervisor cannot safely satisfy, send `NEEDS_HUMAN` and
   surface local review.
7. Mark the run terminal only after the outbound message ID is durable.

Do not auto-approve sandbox escapes or permission requests from inbox-created
tasks. Execution permissions come from recipient-owned Codex configuration.
If that policy cannot finish unattended, the correct result is
`NEEDS_HUMAN`.

### 6. Deliver inbox results to existing sessions

When a local Codex task sends a proposal through the shared-task skill, require
or derive its local source Codex thread ID and persist the outbound
correlation. Later `TASK_ACCEPTED`, `PROGRESS`, `RESULT`, or `NEEDS_HUMAN`
messages on that Cloud SQL thread use the sender-side mapping.

Delivery rules:

- `TASK_ACCEPTED` and routine `PROGRESS` update local status and cockpit state;
  they need not wake the source task for every edge.
- `RESULT` and `NEEDS_HUMAN` queue a local session delivery.
- If the target Codex task is idle, call `thread/resume` and `turn/start` with a
  deterministic `clientUserMessageId` and an external-result provenance
  wrapper.
- If it is active, defer until idle; do not steer the active turn by default.
- If the mapping is absent or the exact thread cannot be proved, surface the
  message in human review/cockpit and do not guess.

The resumed Codex task may decide to finish, ask a human, or send another
proposal. A new accepted `TASK_PROPOSAL` creates a new recipient task; status
and result messages never create one.

### 7. Worker composition

Keep one process and one cadence. Extend the existing combined tick with
bounded stages rather than adding a second daemon:

```text
poll/route Cloud SQL
  -> reconcile ambiguous local jobs
  -> dispatch accepted jobs up to capacity
  -> monitor mapped Codex runs
  -> deliver correlated results to idle existing tasks
  -> publish body-free cockpit status
```

Retain the current process lock. One tick must remain time-bounded; a slow
app-server operation must not prevent future inbox polling indefinitely.

## Code changes by area

### Inbox contract and policy

- `src/codex_supervisor/inbox/models.py`: add the strict v1 task payload model.
- `src/codex_supervisor/inbox/routing.py`: add policy-aware `ACCEPT_TASK` without
  changing safe routes for other message kinds.
- `src/codex_supervisor/inbox/service.py`: split receipt handling from durable
  task acceptance, result correlation, and dispatch.
- `src/codex_supervisor/inbox/config.py`: load execution mode, workspace policy,
  capacity, and bounded monitoring intervals.
- `src/codex_supervisor/inbox/protocol.py` and `postgres.py`: preserve the fixed
  stored-function boundary; add a delivery read/reconciliation function only if
  restart recovery cannot be satisfied from the accepted local payload.

### Codex app-server adapter

- `src/codex_supervisor/codex.py`: add typed methods for `thread/start`,
  `turn/start`, `thread/resume`, and owned-thread status/result reads.
- Keep raw inbox envelopes out of the adapter. It accepts only resolved local
  execution parameters and locally generated prompts.
- Add explicit error classification: definitely-unsent, rejected,
  overloaded/retryable, and transport-ambiguous.

### State and orchestration

- `src/codex_supervisor/state.py`: add the run, outbound-correlation, and
  session-delivery tables plus compare-and-transition methods.
- Add a focused module such as `src/codex_supervisor/inbox/execution.py` for the
  state machine. Do not grow `_claim_and_handle` into the Codex run monitor.
- `src/codex_supervisor/worker.py` and `cli.py`: compose bounded dispatch,
  monitoring, and existing-session delivery into the single worker.
- `src/codex_supervisor/cockpit.py`: remove the blanket “generic mutation is
  disabled” blocker and replace it with body-free execution mode, queued,
  running, ambiguous, and failed counts.

### Sender workflow

- `.agents/skills/send-shared-inbox-task/`: add `workspace_key`, expected result,
  and source Codex thread correlation while preserving recipient resolution and
  idempotency.
- `supervisor inbox send-task`: return and persist the outbound correlation in
  the same local operation that queues the Cloud SQL message.
- Update operator documentation so `queued`, `accepted`, `started`, and
  `completed` remain distinct terms.

## Implementation phases

### Phase 1: Contract and local queue, execution still off

- Add v1 payload validation, workspace configuration, tables, and policy tests.
- Update the send skill and CLI to emit v1 and record source correlation.
- Keep `SUPERVISOR_INBOX_EXECUTION_MODE=review` in installed configuration.
- Exit when a trusted proposal can be deterministically classified as
  executable but still produces no app-server mutation.

### Phase 2: One-shot Codex creation canary

- Implement typed app-server create/start/read methods and the execution state
  machine.
- Add `supervisor inbox execute-once --delivery-id ...` for one reviewed
  proposal.
- Prove exact returned Codex thread and turn IDs, prompt provenance, workspace,
  final-result extraction, and Cloud SQL reply correlation.
- Fault-inject every boundary around SQLite commit, `thread/start`,
  `turn/start`, and `inbox_send_message`.

### Phase 3: Persistent dispatch and monitoring

- Enable accepted local queuing, capacity-aware dispatch, restart recovery,
  and terminal result delivery in the existing 30-second worker.
- Add bounded backoff for definite transient failures.
- Keep transport-ambiguous outcomes terminal for automatic handling.

### Phase 4: Existing-session continuation

- Deliver terminal correlated messages to the exact idle source Codex task.
- Defer while active and reconcile after restart.
- Verify that `RESULT` cannot create a fresh thread when correlation is absent.

### Phase 5: Two-workstation rollout

- Enable `allow_unattended_execution` only for the intended Alice/Frank grant.
- Install matching logical workspace mappings on both recipients.
- Enroll a new execution canary tied to app-server adapter identity, Codex
  version/schema fingerprint, local principal, instance ID, and workspace
  policy fingerprint. Do not reuse the deterministic-receipt canary as proof of
  agent execution.
- Run Alice -> Frank and Frank -> Alice tasks, then a bounded multi-round test
  that creates follow-up proposals from returned results.
- Remove the test-only round bound; production retains capacity/backpressure
  but no hop limit.

## Verification matrix

### Unit and state-machine tests

- trusted v1 proposal accepted; v0, untrusted, expired, malformed, and unknown
  workspace proposals fail closed;
- duplicate delivery creates one local job, one accepted reply, one Codex
  thread, and one initial turn;
- results and progress never create Codex threads;
- active existing tasks defer correlated delivery; idle exact matches resume;
- capacity queues work without declining or repeatedly claiming it;
- bodies never appear in logs, status, cockpit snapshots, or audit tables;
- every legal status transition is tested and illegal transitions fail closed.

### Fault-injection tests

Kill or disconnect the worker:

- before and after local acceptance commit;
- before send, after send, and after response for `TASK_ACCEPTED`;
- before send, after send, and after response for `thread/start`;
- before send, after send, and after response for `turn/start`;
- while a Codex turn is active;
- before and after terminal `RESULT` send; and
- before and after delivery into an existing source task.

Each case must recover to exactly one side effect or a visible `AMBIGUOUS`
record. No case may silently start a second Codex thread.

### Real canaries

1. Send one proposal and prove exactly one new Codex task on the recipient.
2. Resend with the same idempotency key and prove no new task or turn.
3. Restart both the supervisor and app-server during a run and reconcile.
4. Return a result and prove it resumes the exact originating task.
5. Run at least ten alternating proposals across both supervisors and prove
   stable Cloud SQL thread correlation, bounded active concurrency, and no
   duplicate sessions.
6. Leave the worker running across reboot and repeat the single-task canary.

## Acceptance criteria

This track is complete when:

- a trusted v1 `TASK_PROPOSAL` is durably accepted and creates exactly one Codex
  app-server thread and initial turn in the recipient-selected workspace;
- the recipient sends durable accepted and terminal messages on the same Cloud
  SQL conversation;
- a result resumes the exact mapped existing sender-side Codex task when idle;
- either supervisor can generate a follow-up task, allowing indefinite useful
  coordination without a production hop limit;
- duplicate polling, message retries, process restarts, and ambiguous transport
  failures do not duplicate Codex work;
- capacity limits produce a durable queue rather than silently dropping or
  declining trusted work;
- untrusted senders, malformed payloads, unknown workspaces, missing mappings,
  and permission requests fail closed into visible review; and
- the installed service, operator status, cockpit, tests, and two-workstation
  canaries all reflect the real enabled behavior.

## Non-goals

- No Cloud Run, Pub/Sub, webhook, SMTP, Matrix, A2A, or new transport.
- No raw Cloud SQL table access from the supervisor.
- No sender-selected local filesystem path or Codex permission policy.
- No automatic approval of sandbox escapes or secret access.
- No claim that Cloud SQL and app-server provide a shared exactly-once
  transaction.
- No global conversation-depth limit: indefinite coordination is intentional.
