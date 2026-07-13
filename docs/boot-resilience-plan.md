# Boot and Liveness Plan

## Executive Summary

- **Goal:** Make the supervisor start automatically after login/boot and make failures of its launcher or manager observable, while preserving the project's fail-closed reply policy.
- **Steps:**
  - Add a small, non-mutating `doctor` command with machine-readable checks and useful exit codes.
  - Package a per-user service definition for the supported host platform(s), with restart throttling and explicit runtime paths.
  - Add an optional watchdog/health timer that detects a stale launcher or manager and records or alerts, never sends replies.
  - Prove the behavior in disposable shadow-mode canaries before any reply-capable service is enabled.

## Loop Continuation Contract

- **Continue while:** A bounded implementation or verification step can improve the launch/liveness contract without enabling reply mutation or requiring a host-specific user decision.
- **End condition:** `doctor` validates the installed service and runtime dependencies; the service starts after a login/boot simulation, restarts after a controlled crash without a restart loop, and a stale heartbeat triggers the configured non-mutating failure signal. Non-success states are unsupported platform, missing installation/configuration, unavailable Codex app-server socket, or a user-required decision on service scope and notifications.
- **Manual human inputs:** Choose the supported target platform(s) and approve installing/enabling a persistent per-user service. If notifications are desired, choose the delivery channel and grant any required permission.
- **Human-gate impact:** Platform choice blocks final packaging; enabling a persistent service is an approval gate but does not block implementation or shadow-mode testing.
- **Return timing:** Before writing/enabling an OS service; otherwise after each bounded implementation slice.
- **Safe default if unanswered:** Implement Linux `systemd --user` artifacts only, leave them disabled, and make all health failures visible in status/logs without sending notifications or replies.
- **Retry/wait bounds:** Let the OS manager restart a failed service with a 30-second delay and a burst limit; the watchdog performs one bounded status probe per interval and records/alerts once per failure episode, then backs off until recovery. It never restarts blindly more than once per interval.

## Goal

### Success criteria

1. The supervisor has an explicitly configured, inspectable owner process that starts after the user session is available, not merely because a terminal happened to be open.
2. A crash exits the worker and is restarted by exactly one process supervisor; overlapping `scan-once` invocations remain protected by the existing nonblocking lock.
3. `codex-unread-supervisor doctor --json` distinguishes install/configuration faults, service-manager state, socket reachability, state-store health, and actual supervisor progress.
4. A liveness failure is observable through an exit status, structured diagnostics, logs, and optionally an operator-approved notification; it cannot silently enable replies or clear human-review markers.

### Evidence that proves completion

- Unit tests cover diagnostic classifications and heartbeat freshness rules.
- A disposable shadow-mode instance is shown to start after `systemctl --user start` (and after a reboot/login test), recover after a controlled process exit, and stop retrying after the configured burst limit.
- `doctor --json`, `status`, journal logs, and the watchdog output agree on healthy, degraded, and failed fixtures.

## Scope

### In scope

- Extend the CLI with `doctor`, `service-install`, `service-status`, and `service-uninstall` (or an equivalent clearly namespaced service subcommand).
- Introduce a long-lived manager/worker mode that schedules bounded `run_once` ticks, emits a durable heartbeat, and exits nonzero for unrecoverable local faults.
- Provide Linux `systemd --user` templates first; add macOS `launchd` only after validating the host and its login/session constraints.
- Add an independent health timer/service or watchdog mode that evaluates service status and heartbeat freshness.
- Document installation, logs, repair steps, and the distinction between service health and reply authorization.

### Out of scope

- Enabling production replies, guessing the missing inventory or Claude/Fable adapter, or changing unread semantics.
- Root/system-wide services, cron as the primary supervisor, automatic state reset, and automatic recovery from ambiguous delivery.
- A self-restarting worker plus a separate OS restarter (two competing restart authorities).

## Approach

### 1. Define process roles and state boundaries

Use one OS manager as the restart authority and three application roles:

| Role | Responsibility | May mutate task state? | Failure action |
| --- | --- | --- | --- |
| Worker/manager | Runs periodic bounded scans in shadow mode or explicitly enabled mode; writes heartbeat only after a completed tick. | Existing documented state writes only | Exit nonzero for unrecoverable startup/runtime faults; OS manager decides restart. |
| Doctor | One-shot diagnostic report; no repair or reply delivery. | No, except SQLite read-only access | Exit 0 healthy, 1 degraded/action needed, 2 failed/unusable. |
| Watchdog | Compares service-manager state and heartbeat age; emits a single failure episode signal. | Optional local incident record only | Does not issue replies or reset markers; optional approved restart is a later, explicit policy. |

The worker should keep the existing `run_once` file lock. The heartbeat must include schema version, PID/instance ID, last tick start/finish timestamps, result category, and the configuration mode (`shadow_mode`, reply enablement, canary status), written atomically outside SQLite or in a separate SQLite table/transaction.

### 2. Build the doctor as a layered, fail-closed probe

Implement `codex-unread-supervisor doctor [--json] [--strict]` with no network calls and no reply path. Checks should be independently classified rather than collapsed into a generic “healthy” result:

1. **Installation:** executable/version, Python environment, configured state and runtime directories, and safe file permissions.
2. **State:** open SQLite read-only, run a lightweight integrity/read query, report database path, WAL condition, terminal-marker count, and last heartbeat (without secrets).
3. **Configuration:** validate configured service command, absolute executable/working-directory paths, interval, state path, socket path, and confirm that reply eligibility remains disabled unless all three existing flags are explicitly true.
4. **Dependencies:** test that the Unix socket path exists and is a socket; do not treat socket presence as proof that Codex semantics are verified.
5. **Runtime progress:** heartbeat age compared to `tick_interval + grace`; classify no heartbeat, stale heartbeat, last-tick failure, and healthy progress separately.
6. **Service manager:** when running on a known platform, ask `systemctl --user` or `launchctl` for the named unit's loaded/enabled/active state and PID; degrade, rather than crash, when the manager is unavailable (for example a foreground development run).

The human-readable report should lead with the actionable fault and the exact log/status command. `--strict` returns nonzero for any degraded result so timers and CI can consume it.

### 3. Add a single managed worker service

Add `run`/`serve` mode with a configured tick interval. Each iteration should catch expected transient scan errors, record a bounded failure result, and wait for the next interval; invariant-breaking setup errors should exit nonzero. Do not daemonize internally.

For Linux, generate and install a `systemd --user` unit such as `codex-unread-supervisor.service` using absolute paths. It should use `Restart=on-failure`, `RestartSec=30`, `StartLimitIntervalSec`/`StartLimitBurst`, a controlled environment file, and journald logging. Start after the graphical/user session and, only if socket behavior requires it, bind to an appropriate user-session target rather than assuming a socket survives early boot. Enable linger only with explicit user approval because it changes behavior after logout.

The installer must be idempotent and produce a preview/dry-run before writing under the user's service directory. It must never use root privileges or embed secrets in the unit file; an `EnvironmentFile` must be owner-readable only.

### 4. Add watchdog and recovery policy

Use a separate `systemd --user` timer plus one-shot `watchdog` service (or equivalent `doctor --strict` invocation) every 2–5 minutes. It verifies both the unit state and heartbeat freshness, then writes an incident record/log entry with a stable incident key. Suppress repeats until a later healthy result closes the episode.

Phase 1 only detects and reports. Phase 2 may request `systemctl --user restart` only when all of these are true: the unit is in a failed/inactive state, the incident has not exceeded its restart budget, the service is shadow-only or separately approved, and the action is logged. A stale heartbeat while the worker is active should be reported for human review first: it may be an in-progress scan, a deadlock, or an app-server hang, and killing it can create ambiguous delivery.

### 5. Roll out safely

1. Ship diagnostics and tests first; no persistent unit is enabled by default.
2. Install on one disposable user profile in `shadow_mode=True`; verify doctor output and structured logs.
3. Exercise controlled worker failure, stale-heartbeat, unavailable-socket, SQLite-lock, and restart-burst scenarios.
4. Verify cold-login/boot behavior on the actual target OS; document whether it is login-scoped or headless/linger-scoped.
5. Require the existing unread/canary evidence and a reviewed production adapter before considering any non-shadow worker. Service health approval is separate from reply enablement.

## Interfaces And State

### CLI and files

- `doctor --json [--strict]`: structured report with stable check IDs, status (`ok`, `degraded`, `failed`, `unknown`), remediation, and no secrets.
- `serve --interval SECONDS`: foreground worker for the OS manager; validates configuration before loop start.
- `service install|status|uninstall`: explicit per-user lifecycle, with `--dry-run` for install.
- `watchdog --strict`: one-shot liveness evaluation for a service timer.
- State additions: a versioned heartbeat/incident record and optional runtime lock/identity file; terminal human-review state remains authoritative and untouched by health tooling.

### Compatibility rules

- Existing `status`, `scan-once`, and `canary-readiness` behavior remains fail-closed.
- Service commands must work with a checkout/virtualenv that has moved only when the operator reruns `service install`; doctor reports stale paths instead of guessing.
- The service must use the same state path and configuration source as the CLI; mismatches are a failed diagnostic.

## Verification

### Automated checks

- Unit tests for exit codes, JSON schema, stale/fresh heartbeat boundary, incident deduplication, migration/version errors, permissions, and no mutation from doctor/watchdog.
- Subprocess tests with a temporary systemd-unit renderer; validate absolute paths, `Restart=on-failure`, restart delay/burst limits, and no secret-bearing output.
- Integration fixture for the worker loop: it writes heartbeat only after lock acquisition and a completed tick, and it preserves the existing overlap behavior.

### Manual acceptance gates

- Inspect generated unit/timer before installation and confirm the intended account, state path, environment file, and boot/login behavior.
- On the target machine, demonstrate `service-status`, `doctor --strict`, controlled crash restart, intentional stale heartbeat alert, recovery clearing the incident, and a restart-burst stop.
- Keep reply mutation disabled throughout acceptance. If later enabling replies, repeat the README's disposable-task canary separately.

## Rollout Or Handoff

### Sequence and ownership

1. **Implementation:** add health model, heartbeat store, doctor, and tests.
2. **Packaging:** add systemd user service/timer renderer and explicit installer, initially disabled.
3. **Operator validation:** install on the actual host in shadow mode and collect journal/doctor evidence.
4. **Decision gate:** choose macOS support and whether approved notifications or automated recovery are needed.

### Risks and open decisions

- The repository currently has no reviewed production adapter or verified `hasUnreadTurn` source; therefore startup reliability alone cannot make it operationally safe to reply.
- “Boot” needs a platform decision: desktop Codex/app-server access likely requires a logged-in GUI user session, while a headless always-on setup needs explicit `systemd` linger plus a proven socket lifecycle.
- The desired alert destination is unknown. Default to local journal plus `doctor --strict`; do not introduce messaging credentials or notifications until chosen.
- Hermes is a useful operational model—its gateway can install per-profile systemd/launchd services and exposes `doctor`—but do not copy its broad gateway behavior or treat its doctor as a liveness guarantee. Its public issue history also shows that a passing doctor can coexist with a failed gateway, which is why this plan requires a heartbeat and an independent watchdog.

### Stop, wait, and rollback states

- **Stop:** any ambiguity around reply delivery, missing unread verification, or a doctor/watchdog code path that can mutate task state.
- **Wait:** unsupported/missing OS service manager, unavailable user session/socket, or operator approval for persistent installation/notifications.
- **Rollback:** `service uninstall` stops/disables the unit and timer; retain SQLite markers/audit state and logs for inspection. Return to foreground `scan-once`/shadow operation only.
