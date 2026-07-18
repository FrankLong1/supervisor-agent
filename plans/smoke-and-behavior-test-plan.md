# Smoke And Behavior Test Plan

> **Historical document.** Its proposed shadow-mode test organization predates
> the simplified controller and dry-run command. Use the current
> [supervisor specs](../specs/README.md) and the repository's existing test
> suite as the implementation contract.

## Executive Summary

- **Goal:** Add a fast, deterministic offline smoke suite for the supervisor, then make the existing behavioral coverage easier to maintain and extend.
- **Steps:**
  - Establish a small smoke suite for imports, safe CLI paths, defaults, and unit rendering.
  - Preserve existing safety assertions while splitting the monolithic test module by responsibility.
  - Add focused boundary tests for inventory verification, terminal state, and delivery lifecycle.
  - Make smoke and full-suite commands clear in project documentation.

## Loop Continuation Contract

- **Continue while:** A bounded test-planning or implementation step can improve offline confidence without touching a real Codex task, app-server socket, systemd user installation, or reply-capable configuration.
- **End condition:** The plan is implemented only when the smoke suite and the complete offline suite pass, test names map to concrete safety properties, and the README lists both commands. Non-success return states are test failures, contradictory current behavior, missing local dependencies, or a request to add a live integration environment.
- **Manual human inputs:** None after checking: approvals, credentials, access, external confirmations, user-visible actions, and business decisions. A future opt-in live integration suite requires the user to choose a disposable environment and explicitly approve any real Codex interaction.
- **Human-gate impact:** None for the offline suite; blocks any live/integration-test expansion.
- **Return timing:** After the bounded implementation and local test run; immediately if existing behavior contradicts this plan or a test would require a real external service.
- **Safe default if unanswered:** Build only hermetic tests using temporary files and fake adapters; do not contact a socket, systemd, Claude, or Codex.
- **Retry/wait bounds:** Retry a deterministic test command once after inspecting the failure. Do not loop on missing dependency installation or external-service failures; report the exact command and blocker.

## Goal

### Success criteria

1. Contributors have one fast command that proves the package imports and default-safe CLI behavior remains intact.
2. The full test suite keeps the current fail-closed guarantees around unread inventory, classifier decisions, state markers, and reply delivery.
3. Tests are organized so a future change has an obvious home without changing their behavior during a mechanical move.

### Evidence that proves completion

- `uv run --with pytest pytest -q tests/test_smoke.py` passes.
- `uv run --with pytest pytest -q` passes.
- Smoke tests use only temporary paths and fakes; no external socket, model, real task, or service manager is contacted.
- The README documents the smoke and full-suite commands.

## Scope

### In scope

- Add `tests/test_smoke.py` for lightweight public-surface checks.
- Move or refactor existing tests by responsibility while retaining their assertions and fake behavior.
- Add concise parameterized boundary cases where they make safety coverage clearer.
- Update test-running guidance in `README.md`.

### Out of scope

- Production Codex app-server, Claude/Fable, systemd, or desktop integration tests.
- Network access, credentials, persistent service installation, or task/reply mutation.
- Changing supervisor delivery semantics or enabling automated replies.
- Chasing coverage percentages as a goal independent of risk.

## Approach

### 1. Establish the smoke-test contract first

Create `tests/test_smoke.py` using `unittest` and temporary paths, matching the existing project style. Keep it to roughly 5–8 tests that collectively verify:

- every `codex_supervisor` module imports;
- CLI argument parsing exposes help without initializing state;
- `doctor` remains read-only when the state database and socket path do not exist;
- `status` can initialize and report a temporary state database;
- default `SupervisorConfig` is shadow mode with replies disabled;
- `render_units()` produces the expected worker and watchdog unit names without installing them.

Capture CLI output with `contextlib.redirect_stdout`/`redirect_stderr`; invoke `main([...])` directly instead of spawning a shell. This keeps the suite independent of entry-point installation and avoids platform-specific process behavior.

### 2. Preserve coverage while separating responsibilities

Leave assertions unchanged during the first reorganization. Extract reusable `FakeInventory`, `FakeCodex`, `FakeSession`, and `RecordingAppServer` into `tests/fakes.py`, then divide tests according to the module or behavior under test:

| File | Coverage |
| --- | --- |
| `tests/test_smoke.py` | Public import, CLI-safe paths, defaults, rendered-unit presence |
| `tests/test_scanner.py` | Eligible unread candidates, verified inventory requirements, own-thread exclusion |
| `tests/test_supervisor.py` | Classifier decisions, context bounds, shadow behavior, terminal markers, delivery safety |
| `tests/test_state.py` | Restart persistence, atomic claims, timeout/clearance state transitions |
| `tests/test_cli.py` | Exit codes, read-only diagnostics, reset validation, JSON output shape |
| `tests/test_health_service.py` | Heartbeat freshness and systemd unit rendering |

Do this as a mechanical move first. If an existing test reveals a real defect while moving, stop the reorganization and isolate that defect in a separately named regression test rather than silently changing expected behavior.

### 3. Add high-value boundary tests

After the split is green, add only cases directly tied to fail-closed policy:

1. Any missing, malformed, or non-integer `unreadAt` causes the scan to be unsupported and prevents context/classifier work.
2. Empty inventory and self-only inventory never invoke the classifier.
3. Invalid decision JSON, incomplete decision shapes, context exceptions, and classifier exceptions produce a terminal human-review state outside shadow mode.
4. A successful transport acknowledgement cannot result in a second send before the exact unread receipt clears; changed `unreadAt` is a new receipt.
5. Canary evidence is bound to the active inventory and delivery identities; mismatches block delivery.
6. CLI command validation reports invalid interval/grace and missing `--thread-id` without mutating unrelated state.

Use small table-driven subtests or `unittest` loops for variants that share the same safety property. Avoid tests that duplicate an assertion already made by a lower-level test unless they cover a distinct public CLI boundary.

### 4. Keep test execution discoverable

Add the following developer guidance near the existing setup commands in `README.md`:

```bash
# Fast offline confidence check
uv run --with pytest pytest -q tests/test_smoke.py

# Full deterministic test suite
uv run --with pytest pytest -q
```

State explicitly that both commands are offline and do not connect to Codex or send replies.

## Interfaces And State

### Test isolation

- Each state test creates a `SupervisorState` backed by `tempfile.TemporaryDirectory()` and closes it in cleanup.
- Fakes expose calls and configured failure modes so assertions verify absence of side effects as well as results.
- Tests pass temporary state paths, socket paths, and unit directories explicitly; they do not use `default_state_path()` for filesystem writes.
- Tests should not depend on wall-clock sleeps. Heartbeat staleness uses a fixture timestamp or a zero grace/timeout as already established by the suite.

### Naming and assertion policy

- Name tests as observable guarantees (`test_missing_unread_receipt_fails_closed`), not internals (`test_scan_3`).
- Assert exit code, durable marker/claim state, and externally visible output only where each is relevant.
- Keep fake adapter details in `tests/fakes.py`; production code remains free of test-only branches.

## Verification

### Automated checks

1. Run `uv run --with pytest pytest -q tests/test_smoke.py` after adding the smoke suite.
2. Run `uv run --with pytest pytest -q` after each mechanical test-file move and after boundary additions.
3. Use `git diff --check` to catch whitespace errors in tracked changes, then inspect every newly added test file for trailing whitespace (untracked files are not covered by `git diff --check`).
4. Inspect `git status --short` together with `git diff -- tests README.md` to confirm no production behavior changed as part of test work.

### Manual review gate

Before merging, verify that every new test is hermetic: no real Unix socket connection, no subprocess that calls `systemctl`, no environment-specific state path, and no reply-capable `SupervisorConfig` except fake-adapter delivery tests that explicitly prove duplicate prevention.

## Rollout Or Handoff

### Implementation sequence

1. Add and pass the smoke suite.
2. Extract fakes and move existing tests without changing expectations; run the full suite after each file group.
3. Add the prioritized boundary cases and README commands.
4. Run final smoke, full suite, and diff checks; summarize any intentionally deferred integration coverage.

### Risks and stop conditions

- **Stop and report:** a supposedly offline test needs a real socket/service/model, or a current test exposes a production-semantic discrepancy.
- **Defer:** live integration coverage until the project has a reviewed production adapter, verified unread semantics, a disposable task, and explicit user approval.
- **Rollback:** revert only the new/reorganized test and README changes; no database migration or runtime configuration is involved.

### Follow-up trigger

Create a separate, opt-in integration-test proposal only after the inventory and delivery adapters have concrete reviewed contracts. It must specify the disposable environment, cleanup, evidence, and approval gate before any real Codex task is touched.
