# Plan 03: authoritative unread feed

## Outcome

Replace the manually saved `--inventory-snapshot` workflow with a supported,
fresh, read-only feed of the Codex app's real `hasUnreadTurn` state.

This is the hard prerequisite for unattended automation. The workstation
app-server currently exposes runtime status but not client unread state. Until
that changes, a standalone CLI cannot honestly know which idle tasks are
unread.

## User-visible contract

```text
supervisor inventory status --host-id HOST --json
supervisor scan-once --host-id HOST
```

`inventory status` reports source identity, last refresh time, host coverage,
schema version, and freshness. `scan-once` consumes the same feed. Manual
snapshot input remains available only as a diagnostic fixture.

## Source selection

Use supported integrations in this order:

1. an official Codex app/app-server API that exposes `hasUnreadTurn`;
2. a Codex-app-owned bridge that sends the schema-version-2 `list_threads`
   result to a local Unix-socket receiver; or
3. no unattended feed.

Do not scrape private desktop databases, infer unread from `idle`, or silently
fall back to stale snapshots. If no supported source exists, keep later live
plans blocked and report the missing capability explicitly.

## Stored envelope

Persist an atomic, bounded envelope outside the checkout:

```text
schema_version
source_identity
host_id
collected_at
sequence_or_digest
threads[]: id, status, hasUnreadTurn, updatedAt, title
```

The feed is read-only with respect to Codex. Reading or importing it must not
acknowledge an unread result.

## Implementation slices

1. Define an `UnreadFeed` protocol and freshness result independent of the
   current snapshot adapter.
2. Probe the installed Codex interfaces and record which supported transport
   supplies the unread flag.
3. Implement the selected adapter and stable `source_identity`.
4. Add atomic persistence, maximum age, host coverage, and schema validation.
5. Wire `scan-once` to the feed while retaining explicit snapshot fixtures for
   tests.
6. Add `inventory status` and actionable failure output.

## Fail-closed rules

- Missing, stale, malformed, wrong-host, or regressed-sequence data selects
  zero tasks.
- Every idle row must contain a boolean `hasUnreadTurn`.
- Every unread row must contain an integer `updatedAt` receipt.
- Adapter identity changes invalidate later canary evidence.

## Verification

- Contract tests for fresh, stale, malformed, missing-host, and identity-change
  cases.
- A real Codex test task transitions `not unread -> unread -> not unread` and
  the feed observes all three without clearing it.
- Keep the feed running for at least 30 minutes and prove refresh/freshness
  reporting across multiple updates.

## Done when

`supervisor scan-once --host-id HOST` can select a newly unread disposable task
without a human creating a snapshot file, and it still selects nothing when
the authoritative feed is unavailable.
