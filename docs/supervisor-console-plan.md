# Supervisor Console implementation plan

## Executive Summary

- **Goal:** Deliver a local, read-only overnight-status console without adding
  another agent or a path to send replies.
- **Steps:**
  - Add a bounded, append-only event trail to the supervisor's existing state.
  - Project state and heartbeat through a loopback-only read API.
  - Build and verify the three operator views against shadow-mode fixtures.

## Loop Continuation Contract

- **Continue while:** An implementation step can add the event model,
  read-only projection, local UI, or deterministic tests without enabling reply
  mutation or needing a human-only access/security decision.
- **End condition:** The console serves the documented three views from
  read-only supervisor state, automated checks prove the safety boundary, and a
  disposable shadow-mode run matches SQLite facts. Non-success returns are a
  missing/contradictory state schema, local binding failure, raw-data exposure,
  or a request for a mutation/remote-access feature.
- **Manual human inputs:** None for the v1 localhost read-only implementation
  after checking approvals, credentials, access, external confirmations,
  user-visible actions, and business decisions. A request for remote access,
  notification delivery, or review-marker reset requires explicit approval.
- **Human-gate impact:** Those later features block only their own phase, not
  v1 completion.
- **Return timing:** After each bounded implementation slice; immediately if a
  safety boundary cannot be proven.
- **Safe default if unanswered:** Keep the service loopback-only and read-only;
  omit optional controls and external integrations.
- **Retry/wait bounds:** Retry only deterministic local test/setup failures once
  after fixing their identified cause. Do not retry ambiguous state reads,
  expose partial data as success, or wait for external services.

## Goal

Provide a localhost-only operator console that answers one morning question:
**“What did Fable and the supervisor do overnight, what is waiting on me, and
what evidence supports that?”**

The console is a safety-preserving view of the existing supervisor. It does not
become an agent, a scheduler, a task dispatcher, or a way to send Codex replies.

### Success criteria

1. An operator can open a local URL and see last-tick health, overnight
   activity, the human-review queue, shadow decisions, delivery-claim state,
   and reply/canary safety eligibility.
2. Every task view distinguishes observed activity, a proposed action, a
   transport acknowledgement, confirmed unread clearance, and terminal human
   review.
3. The browser never accesses the database, Codex app-server socket, or
   Fable/Claude session directly.
4. The console is read-only in v1. No UI action can mutate a task or send a
   reply.

### Evidence that proves completion

- A local server binds only to loopback and serves the console plus documented
  JSON endpoints.
- Automated tests prove endpoints use SQLite read-only mode, redact sensitive
  fields, and truthfully classify all claim/review states.
- A manual shadow-mode run shows a real overnight-style summary, task audit,
  and health view from fixture or disposable state.
- Existing supervisor tests remain green; no reply path is invoked by console
  code.

## Scope

### In scope

- A `codex-unread-supervisor console` command that starts a local HTTP server.
- Static HTML/CSS/JS UI with three routes/views:
  - **Morning brief:** concise overnight narrative and attention list.
  - **Operations queue:** all recently observed tasks and health/safety state.
  - **Task audit:** durable evidence for one task, especially review markers
    and delivery claims.
- Read-only JSON projection endpoints over SQLite state and heartbeat data.
- A compact, append-only activity-event log that makes the timeline truthful.
- Documentation for start/stop, local access, limitations, and troubleshooting.

### Out of scope

- Hermes Kanban, worker dispatch, card drag/drop, task assignment, or retries.
- General transcript browsing, browser-to-Codex/Fable RPC, or editing a reply.
- Reply enablement, changing unread semantics, or weakening canary requirements.
- Remote hosting, multi-user access, cloud telemetry, analytics, or external
  assets.
- An operator reset button in v1. A future reset flow needs its own explicit
  audit and confirmation design.

## Approach

### 1. Add an append-only console event stream

Current tables expose present state but cannot reconstruct an overnight story.
Add `supervisor_events` as an append-only table and write bounded, redacted
events at these transition points:

- verified unread receipt observed;
- inventory unavailable or invalid;
- context read succeeded or failed;
- Fable decision returned or failed validation;
- shadow decision recorded;
- delivery claim created, acknowledged, cleared, timed out, or interrupted;
- human-review marker created;
- canary evidence recorded or revoked.

Each event has `occurred_at`, optional `(host_id, thread_id, unread_at)`, a
fixed `event_type`, `info|warning|critical` severity, a short summary, and
strictly allowlisted detail JSON. Never store raw task context, full model
output, credentials, or an unmasked session identifier.

### 2. Build a read-only console projection

Implement a `ConsoleRepository` separate from `SupervisorState`:

- opens SQLite using `file:<path>?mode=ro`;
- reads the heartbeat through the existing `read_heartbeat` helper;
- invokes `health.report()` for the doctor-style health summary;
- maps raw rows to a versioned response schema with titles/IDs bounded and
  session/delivery IDs masked;
- returns “unavailable” rather than treating missing state or heartbeat as
  success.

The current heartbeat is liveness-only and does not truthfully identify the
running `SupervisorConfig`. Extend its atomically written, versioned payload
with a non-secret runtime snapshot: `shadow_mode`, `allow_replies`, whether
matching canary evidence is present, scan-result category, and last completed
tick counters. Until that snapshot exists, the console must display reply mode
and scan coverage as **unknown**, not infer them from the database.

Do not let the HTTP process instantiate `Supervisor`, `AppServerClient`, or a
Fable adapter.

### 3. Serve a tiny localhost-only application

Start with the Python standard library (`ThreadingHTTPServer`) unless a web
framework is already adopted for another reason. It should:

- bind to `127.0.0.1` on a configurable port (default `8765`);
- reject non-loopback `--host` values;
- serve packaged static assets with a strict Content-Security-Policy. Put CSS
  and JavaScript in separate local assets; do not weaken CSP with
  `unsafe-inline` just to support the prototype files;
- set `Cache-Control: no-store` on API responses;
- include a local bearer token by default, stored mode `0600` next to runtime
  state. The launcher prints a one-time URL with the token in its **fragment**;
  bootstrap JavaScript reads it, immediately removes it with
  `history.replaceState`, and sends it in an API header. The server never
  accepts tokens in a query string or logs them. A same-user Unix-domain socket
  is a later alternative when browser support is solved;
- log only request path, status, and duration—never tokens or payloads.

### 4. Implement the API and UI in this order

| Step | Endpoint/view | Why it comes first |
| --- | --- | --- |
| 1 | `GET /api/v1/summary` + morning brief | Delivers the overnight-status value fastest. |
| 2 | `GET /api/v1/health` + safety panel | Prevents a fresh heartbeat being mistaken for active scanning. |
| 3 | `GET /api/v1/tasks` + operations queue | Lets an operator locate what needs attention. |
| 4 | `GET /api/v1/tasks/{host}/{thread}` + task audit | Supplies the evidence before a human resets/replies manually. |
| 5 | `GET /api/v1/events?since=` | Adds an activity timeline and incremental refresh. |

Poll summary/tasks every 10–15 seconds initially. Add SSE only if practical
use shows polling is insufficient.

## Interfaces and state

### API contract

`GET /api/v1/summary?since=<RFC3339>` returns:

```json
{
  "generated_at": "2026-07-13T07:32:11Z",
  "mode": {"shadow_mode": true, "reply_eligible": false, "reason": "matching canary evidence is absent"},
  "health": {"overall": "degraded", "heartbeat": "fresh", "state": "ok", "socket": "absent"},
  "counts": {"observed_unread": 14, "shadow_reply": 4, "human_review": 1, "pending_delivery": 0, "confirmed_delivery": 0},
  "attention": [{"host_id": "local", "thread_id": "thr_4rQ…f1", "title": "Fix OAuth callback handling", "reason": "validation status is not established"}]
}
```

All endpoint responses must have a `schema_version` field and return an empty,
explicitly unavailable state when no database exists yet.

### Data ownership

| Source | Writer | Console access |
| --- | --- | --- |
| `state.sqlite3` | Supervisor only | SQLite read-only |
| `.heartbeat.json` | Service worker only | Filesystem read-only |
| console token/runtime file | Console launcher only | owner read/write; `0600` |
| Codex app-server socket | Supervisor adapter only | **none** |
| Fable/Claude session | Supervisor adapter only | **none** |

## Verification

### Automated checks

- Unit test every event emission and schema migration.
- Test console DB connections fail safely when write attempted or DB is absent.
- Fixture-test each API response: normal shadow decision, human review,
  interrupted claim, awaiting clearance, confirmed delivery, corrupted
  heartbeat, and unavailable inventory.
- Assert responses never include raw context, raw model output, tokens, or full
  session IDs.
- Test non-loopback binding is rejected; no endpoint accepts a token in a query
  string; token bootstrap removes its fragment; and responses set the expected
  CSP and no-store headers without `unsafe-inline`.
- Run the existing `python -m unittest discover -s tests -q` suite.

### Manual acceptance

1. Run in shadow mode with a disposable state database.
2. Create one shadow reply, one human-review marker, and one cleared claim
   through fakes/fixtures.
3. Open the local console and verify each state and timeline item match SQLite.
4. Stop the console; verify the supervisor continues unchanged.
5. Inspect console logs and database permissions before any persistent service
   is added.

## Rollout and handoff

### Delivery sequence

1. Add the event table, migration, and tests.
2. Add repository/read model and endpoint tests.
3. Add static UI, initially based on the archived visual concepts.
4. Add `console` command and local manual runbook.
5. Run a one-night shadow-mode observation before considering the optional,
   separately designed reset-review action.

### Stop and rollback states

- **Stop immediately:** any console path can call a reply/adapter method,
  direct browser access to an app-server socket, raw transcript exposure, or
  loopback-binding failure.
- **Wait for operator decision:** remote access, notifications, authentication
  beyond same-user local access, or a reset-review mutation endpoint.
- **Rollback:** stop the console process or disable its service. Keep SQLite
  markers and append-only events for inspection; the supervisor remains safe
  without the console.

## Archived exploration

The earlier HTML sketches are deliberately shelved at
[`docs/archive/console-prototypes/`](archive/console-prototypes/). They are
visual references only, not a committed UI direction. The implementation should
begin with the data contract and safety boundaries above.
