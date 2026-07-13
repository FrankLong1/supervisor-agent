# Supervisor console backend design

## Decision

Run a small **localhost-only read API** beside the supervisor. It projects the
supervisor's SQLite state and heartbeat into a stable, redacted status model for
the console. It is not a second scheduler, does not speak to Codex or Fable,
and cannot send a reply.

The UI must not open the SQLite database directly. A server boundary keeps the
database file private, centralizes schema migrations and redaction, gives the
browser a stable API, and leaves room for a live event stream later.

```
                 read only                     read only
state.sqlite3 ───────────────┐
heartbeat.json ──────────────┼─> console server ─> localhost browser
                             │       :8765
supervisor activity events ──┘

Codex app-server socket  ──> supervisor only
Fable/Claude session     ──> supervisor only
```

## Process and safety boundary

- Bind only to `127.0.0.1` (and optionally `::1`); never `0.0.0.0`.
- Default to a random local bearer token written with mode `0600`, or use a
  Unix-domain socket where practical. Loopback alone is not a security boundary
  when untrusted local processes exist.
- Set a restrictive Content Security Policy and serve no third-party scripts,
  fonts, analytics, or network requests.
- Open SQLite with `mode=ro`; the console server never has a writable database
  connection.
- The only future mutation endpoint is an explicit `reset human review` action.
  It must require a confirmation phrase, pass through the supervisor CLI/state
  layer, write an operator audit event, and never call `send_reply`.
- Do not expose raw Fable transcripts or full session IDs. Store/display short,
  length-bounded summaries and masked identifiers instead.

## API shape

Start with polling every 10–15 seconds. The overnight use case does not warrant
a persistent connection on day one.

| Endpoint | Use | Backing data |
| --- | --- | --- |
| `GET /api/v1/summary?since=...` | Morning brief headline, counts, safety mode, health | heartbeat + aggregate state queries |
| `GET /api/v1/tasks?status=...&limit=...` | Operations queue | task projection + latest event |
| `GET /api/v1/tasks/{host_id}/{thread_id}` | Task-audit screen | marker, claims, decisions, events |
| `GET /api/v1/events?since=<cursor>` | Activity timeline | append-only events |
| `GET /api/v1/health` | Doctor-style checks | existing `health.report()` |
| `GET /api/v1/safety` | Shadow/enabled/canary gate explanation | config projection + evidence |
| `POST /api/v1/tasks/{host_id}/{thread_id}/reset-review` | Later, explicit operator reset | state-layer command, audited |

Example summary response:

```json
{
  "generated_at": "2026-07-13T07:32:11Z",
  "window": {"since": "2026-07-12T22:00:00Z", "until": "2026-07-13T07:32:11Z"},
  "mode": {"shadow_mode": true, "reply_eligible": false, "reason": "matching canary evidence is absent"},
  "health": {"status": "degraded", "heartbeat": "fresh", "socket": "absent"},
  "counts": {"observed_unread": 14, "shadow_reply": 4, "human_review": 1, "pending_delivery": 0, "delivered": 0},
  "attention": [{"host_id": "local", "thread_id": "thr_4rQ…f1", "title": "Fix OAuth callback handling", "reason": "validation status is not established"}]
}
```

## Durable data needed

The current tables already safely support terminal review count/reason,
shadow decisions, canary evidence, and delivery claims. They do **not** retain a
complete chronological record, task title, read attempts, or a delivery ID.
Add an append-only `supervisor_events` table instead of trying to reconstruct a
timeline from mutable rows.

```sql
CREATE TABLE supervisor_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  host_id TEXT,
  thread_id TEXT,
  unread_at INTEGER,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
  summary TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX supervisor_events_task_time
  ON supervisor_events(host_id, thread_id, occurred_at DESC);
```

Record a compact event whenever the supervisor observes an unread receipt,
reads context, gets a decision, creates/acknowledges/confirms a delivery claim,
marks human review, or encounters an unavailable inventory. `detail_json` is
strictly schema-controlled: receipt key, decision kind, masked delivery ID, and
error class are fine; raw task context and model output are not.

Also add `title TEXT` to the event detail at observation time. Titles cannot be
reliably recovered once an archived Codex task disappears from inventory.

## Overnight report logic

The server should report facts with their confidence, not manufacture a success
narrative:

- **Observed unread**: event count, not a claim that work completed.
- **Shadow reply proposed**: a model recommendation, not a sent instruction.
- **Delivered**: only an `AWAITING_CLEARANCE` claim that later becomes
  `CONFIRMED`; never use transport acknowledgement alone.
- **Human review**: terminal until an operator resets it; count it in the
  morning headline regardless of later heartbeat health.
- **Quiet overnight**: state exactly which data source was unavailable. A fresh
  heartbeat with disabled scanning means "scheduler alive," not "tasks watched."

## Implementation sequence

1. Add the append-only event writer to `SupervisorState` and emit events from
   existing paths. Unit-test every terminal and delivery transition.
2. Add a `console` CLI command using a small standard-library or FastAPI HTTP
   server. It opens SQLite read-only and reuses `health.report()`.
3. Replace illustrative mockup JSON with the endpoints above; retain static
   HTML/CSS/JS so the console is easy to package and inspect.
4. Add the explicit reset flow only after the read-only console has been used in
   shadow mode. Require a confirmation and a recorded operator note.
5. Consider WebSocket/SSE only if 10-second polling proves unsatisfying.

## What this deliberately is not

- Not Hermes Kanban, a work dispatcher, or a second agent lifecycle.
- Not a browser-to-Codex/Fable bridge.
- Not a transcript viewer or a place to edit an agent reply.
- Not an authorization bypass around the canary and delivery-claim gates.
