# Resolution

Independent-review findings: none available because the reviewer produced no report despite successful invocation. Classification: follow-up — investigate the reviewer transport separately; it does not block this bounded change because local verification is complete.

Implemented the provider-neutral `SessionAdapter` protocol, provider configuration/factory with the Claude/Fable default, CLI `--provider`, `--provider-command`, and `--provider-model` options, and a stub-provider unit test proving schema-valid output. The authoritative changes are in `src/codex_supervisor/session.py`, `src/codex_supervisor/providers.py`, `src/codex_supervisor/claude.py`, `src/codex_supervisor/cli.py`, and `tests/test_supervisor.py`.

Checks after the change: `uv run --with pytest pytest -q` passed (30 tests); `git diff --check` passed; the tmux-managed supervisor was restarted and `doctor --json` reported readable state, an existing Codex socket, and a fresh heartbeat. The service-manager check remains degraded because no systemd user bus is available in this environment.
