# Resolution

Reviewer-access blocker: follow-up. It does not change the dashboard's behavior or the user-requested presentation.

Replaced the prior paragraph rendering with deterministic sentence grouping into three authoritative bullets in `src/codex_supervisor/console.py`. The durable original reason remains represented across those bullets; short reasons receive explicit empty-context and review-next-step text rather than a misleading fabricated explanation.

Verification: `uv run --with pytest pytest -q` passed (31 tests), `git diff --check` passed, the dashboard process was restarted, and HTTP-rendered output contains all three bold labels. The live supervisor worker was not started or restarted.
