# Git closeout and remaining safety gates

## Current repository state

- Dedicated branch: `codex/unread-task-supervisor`
- Commit created: `68fc093 Build fail-closed Codex unread-task supervisor`
- Branch pushed to `origin/codex/unread-task-supervisor`
- Draft PR creation was intentionally stopped at the user's request.

No further Git action is needed to preserve the implementation. If a PR is wanted later, create a draft PR from `codex/unread-task-supervisor` into `main`.

## Implementation complete locally

- Standalone Python package with app-server Unix-socket JSON-RPC boundary.
- Fail-closed unread scanner; missing or unverified `hasUnreadTurn` data yields no candidates.
- SQLite terminal `HUMAN_REVIEW_NEEDED` markers, one durable supervisor session ID, shadow decisions, and explicit operator reset.
- Process lock, bounded task context, exact decision contract, and conservative ambiguous-delivery behavior.
- Shadow mode by default; production Claude/Fable wiring and automatic replies disabled by default.
- CLI, canary runbook, README, CI workflow, and unit tests.

## Verification completed

```bash
PYTHONPATH=src python -m unittest discover -s tests -q
```

Result: 16 tests passed. The generated Codex app-server schema was also checked for the required idle continuation sequence: `thread/resume`, then `turn/start`; `turn/steer` is used only with an active turn ID.

## Deliberate remaining safety gates

These are operational evidence requirements, not missing code:

1. Provide and review a real local Claude/Fable session-resume adapter. The package intentionally does not guess a CLI/session protocol.
2. Provide a verified read-only task inventory that exposes `hasUnreadTurn`.
3. Run the disposable-task canary and record evidence that scanning preserves the Codex blue dot, native reply consumes exactly one unread result, and the next source-agent result creates a new unread signal.
4. Only then explicitly configure non-shadow mode, `allow_replies=True`, and `canary_verified=True`.

Until those conditions are proven, unattended replies and cron scheduling must remain disabled.
