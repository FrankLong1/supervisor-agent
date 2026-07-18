# Automatic boot supervisor plan

> **Historical document.** Do not enable the old heartbeat/shadow service from
> this plan. Current background operation is specified by
> [recovery and restart](../specs/07-recovery-and-restart.md) followed by the
> [background user service](../specs/08-background-service.md).

## Executive Summary

- **Goal:** Start the already-proven manual supervisor for the logged-in user and make its liveness observable without weakening reply safety.
- **Steps:**
  - Verify generated systemd user units and run heartbeat-only mode first.
  - Exercise service, watchdog, and login/boot failure behavior on the real desktop session.
  - Promote only the separately proven manual supervisor configuration to boot-managed shadow mode, then request delivery approval.

## Loop Continuation Contract

- **Continue while:** Unit generation, health diagnostics, and heartbeat-only service tests can be performed without enabling reply delivery or changing system-wide behavior.
- **End condition:** A real logged-in session proves boot/login startup, heartbeat freshness, watchdog diagnostics, and rollback; otherwise return for unavailable systemd bus, missing socket lifecycle evidence, or approval for linger/production delivery.
- **Manual human inputs:** Explicit approval to enable a persistent user service; real desktop-session boot/login validation; optional decision on post-logout operation and linger.
- **Human-gate impact:** Service enablement blocks automatic start; Fable/canary approval blocks any production task review or reply delivery.
- **Return timing:** Before enabling units, before enabling linger, and before changing from heartbeat-only/shadow to delivery-capable operation.
- **Safe default if unanswered:** Leave installed unit files disabled and keep manual heartbeat-only mode.
- **Retry/wait bounds:** systemd owns bounded restart behavior; do not auto-restart a stale but active worker and do not retry ambiguous delivery.

## Goal

Make the already-proven manual supervisor start automatically for the logged-in user, monitor its liveness, and remain fail-closed if Codex, Fable, unread inventory, or delivery evidence is unavailable.

## Success criteria

### Evidence that proves completion

- The per-user service starts after a fresh login and writes a heartbeat using the intended state path.
- The watchdog detects stale heartbeat or inactive service and reports the condition without sending a Codex reply or restarting an active worker.
- `doctor --json`, `service-status`, and the user journal agree on healthy and failed fixtures.
- The installed service uses the exact reviewed startup wrapper and configuration, not a moved checkout path or guessed environment.
- Automatic reply capability is enabled only after the manual-first plan's Fable and unread canary gates are complete.

## Current state

- Disabled systemd user-unit files are installed in `~/.config/systemd/user/`.
- The unit invokes `scripts/startup.sh`, which is a checkout-stable wrapper around the Python CLI.
- The current runtime lacks an active `systemd --user` bus, so enablement and boot/login testing must occur in the real logged-in desktop session.
- Current `serve` is heartbeat-only; enabling it now is safe but does not create Fable task review.

## Scope

### In scope

- Linux `systemd --user` service and watchdog timer activation after manual validation.
- Explicit environment/state/socket paths, journald inspection, health diagnostics, login/boot tests, and rollback.
- Optional `loginctl enable-linger` decision only if the user wants operation after logout and Codex app-server socket lifetime is proven.

### Out of scope

- Root/system-wide unit installation, implicit linger, autonomous delivery before manual canary approval, and automatic recovery from ambiguous delivery.

## Workstreams

### 1. Verify the service definition before enabling

1. Preview the generated files:

   ```bash
   ./scripts/startup.sh service-install --dry-run --program "$PWD/scripts/startup.sh"
   ```

2. Confirm `ExecStart` is the intended absolute wrapper path, state path is under `$XDG_STATE_HOME`, interval is appropriate, and no secrets are embedded.
3. Confirm service runs one foreground worker and systemd is the only restart authority.

### 2. Enable safe heartbeat mode on the real user session

1. From the actual logged-in desktop session, reload units and enable them:

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now codex-unread-supervisor.service codex-unread-supervisor-watchdog.timer
   ```

2. Verify:

   ```bash
   ./scripts/startup.sh service-status
   ./scripts/startup.sh doctor --json
   journalctl --user -u codex-unread-supervisor.service --since "10 minutes ago"
   ```

3. Keep this phase heartbeat-only until manual-first validation is complete.

### 3. Exercise liveness behavior

1. Stop the service deliberately and verify systemd restart behavior respects its 30-second delay and burst limit.
2. Make the heartbeat stale in a disposable state directory; verify the watchdog reports degradation without killing a live process or mutating Codex tasks.
3. Test unavailable Codex socket and read-only/corrupt SQLite fixtures; each must be observable through `doctor` and logs.
4. Restart/recover and confirm the next fresh heartbeat returns diagnostics to healthy.

### 4. Perform boot/login acceptance

1. Log out/in or reboot the machine while Codex desktop is available.
2. Confirm the unit starts only after the user session is usable and does not assume the app-server socket exists too early.
3. Confirm the socket check becomes healthy once Codex starts; it must never treat an absent socket as permission to substitute another transport.
4. Decide whether post-logout operation is needed. If yes, separately approve `loginctl enable-linger` and prove Codex/app-server socket availability in that mode.

### 5. Promote from heartbeat to production supervision

1. Complete every gate in `manual-first-live-supervisor.md`.
2. Configure the exact Fable, inventory, and delivery adapter identities and matching durable canary evidence.
3. Start in boot-managed shadow mode, review logs/audit decisions, then explicitly approve non-shadow delivery.
4. Re-run the canary after any adapter, Codex version, or unread-schema change.

## Stop and rollback

Disable and stop all background processes without deleting state:

```bash
systemctl --user disable --now codex-unread-supervisor.service codex-unread-supervisor-watchdog.timer
./scripts/startup.sh service-uninstall
```

Retain the SQLite state database, delivery claims, human-review markers, canary evidence, and journal logs for diagnosis. Do not auto-clear any marker or retry an ambiguous delivery.
