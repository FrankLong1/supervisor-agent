# Resolution

Reviewer access issue: follow-up. The Claude review helper needs a login; this is not repaired within the dashboard scope.

Implemented the authoritative dashboard update in `src/codex_supervisor/console.py`: task IDs were removed, review rows became readable cards, the first real sentence of the reason is bold, and timestamp metadata is visually secondary. Added `supervisor_fable_turns` accounting in `src/codex_supervisor/state.py` and records a successful Fable decision in `src/codex_supervisor/supervisor.py`; historic decisions are explicitly displayed as “not recorded.”

Verification: 31 tests passed, `git diff --check` passed, the local console was restarted, and the rendered dashboard returned HTTP 200 without ID markup. The supervisor worker was intentionally not restarted because it can send live replies and the user asked only for dashboard presentation.
