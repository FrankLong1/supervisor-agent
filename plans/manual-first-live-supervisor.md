# Manual-first live supervisor plan

> **Historical document.** Do not implement its shadow/non-shadow rollout.
> The replacement is the ordered sequence from
> [authoritative unread feed](../specs/03-authoritative-unread-feed.md) through
> [explicit continue-once](../specs/05-explicit-continue-once.md).

## Executive Summary

- **Goal:** Prove a manual, shadow-mode Fable supervisor can safely evaluate Codex unread tasks before any reply delivery is considered.
- **Steps:**
  - Verify the real Fable create/resume contract and one shared supervisor session.
  - Verify a read-only `hasUnreadTurn` inventory and run a bounded shadow session.
  - Prove blue-dot and native-delivery semantics on disposable tasks before requesting reply enablement.

## Loop Continuation Contract

- **Continue while:** A local adapter or shadow/canary check can be verified without delivering a Codex reply or requiring a human-only decision.
- **End condition:** The foreground shadow run, Fable session reuse, and disposable-thread evidence are recorded; otherwise return with the missing contract, unavailable unread state, invalid output, or canary failure.
- **Manual human inputs:** Operator access to Fable, a disposable Codex task, and explicit approval before any non-shadow delivery.
- **Human-gate impact:** Blocks reply enablement and completion of the live-review rollout; it does not block heartbeat-only service operation.
- **Return timing:** After each bounded adapter verification or immediately on unread/delivery ambiguity.
- **Safe default if unanswered:** Remain heartbeat-only and do not inspect, classify, or reply to tasks.
- **Retry/wait bounds:** Do not retry ambiguous delivery; retry a local adapter probe once after changed evidence, then return for human review.

## Goal

Run the Codex unread-task supervisor manually on this machine in a controlled shadow deployment, then prove the exact prerequisites required for it to review unread Codex tasks with Fable and, only after separate approval, send replies.

## Success criteria

### Evidence that proves completion

- A foreground command starts the supervisor and emits a fresh heartbeat.
- The deterministic inventory identifies only unarchived threads with the verified Codex blue-dot/unread signal, without clearing it.
- One dedicated, persistent Fable supervisor session is created once and resumed for every eligible source task and later tick.
- Shadow runs record proposed decisions but do not send messages or write terminal human-review markers.
- A disposable-thread canary proves scan preservation, one native reply consuming one unread result, and a later source-agent result producing a new unread signal.
- A non-shadow run remains disabled unless the reviewed adapter identities and durable canary evidence match the running configuration.

## Current state

- `scripts/startup.sh` starts the safe heartbeat-only `serve` process and is running manually now.
- The Codex app-server Unix socket is present and the heartbeat/state checks are healthy.
- No production Fable adapter is configured, no `hasUnreadTurn` inventory is verified, and no canary evidence exists.
- Therefore the current process does **not** inspect tasks, invoke Fable, or reply to Codex threads.

## Scope

### In scope

- A reviewed Fable adapter that can start once, persist its session ID, resume that same session, send the bounded source-task context, and return exactly one decision JSON object.
- A read-only Codex inventory adapter with a documented `hasUnreadTurn` source and a stable unread receipt key.
- Foreground/shadow execution, structured logs, state inspection, and manual canary evidence collection.
- Reconciliation of the current per-task session-ID code with the original design: the production path must persist **one global Fable supervisor session ID**, not one Fable session per Codex task.

### Out of scope

- Cron, boot enablement, broad automatic replies, fingerprint-based work detection, or any guessed Fable CLI contract.
- Clearing `HUMAN_REVIEW_NEEDED` markers except through the explicit operator reset command.

## Workstreams

### 1. Verify the Fable contract

1. Locate the actual local Fable executable and inspect its help/version plus session create/resume behavior.
2. Run a disposable Fable session manually and capture the exact input, output, session-ID, error, timeout, and exit-code contract.
3. Implement the adapter only from that evidence; never parse prose as a decision or silently create a replacement session.
4. Add tests proving one shared session ID is passed for two source tasks and across two cycles.

### 2. Verify the unread inventory

1. Identify a read-only Codex app-server or local-state endpoint that returns `hasUnreadTurn` for every unarchived task.
2. Confirm by inspection that inventory reads do not acknowledge, mutate, or clear the Codex blue dot.
3. Add a concrete identity/version for the inventory adapter; a missing field or mixed schema must yield no eligible tasks.

### 3. Run manual shadow mode

1. Start the foreground worker with `./scripts/startup.sh serve --interval 60` after the reviewed adapters are wired.
2. Run one bounded scan manually and inspect its state/audit records and Fable session reuse.
3. Confirm that all proposed `REPLY` results are recorded only as shadow decisions and no terminal marker or Codex message is produced.
4. Stop the process cleanly and inspect `doctor --json`, state, and logs.

### 4. Run the disposable-thread canary

1. Create one disposable Codex thread and produce a visible unread result.
2. Scan it through the verified inventory; confirm its blue dot remains visible.
3. Enable a one-thread, reviewed delivery path and send exactly one Fable-approved reply.
4. Confirm the original unread result clears, is not selected on the next scan, and the source agent's next result appears as a new unread signal.
5. Route a separate canary result to `HUMAN_REVIEW_NEEDED` and confirm its blue dot remains visible.
6. Store immutable canary evidence linked to the exact inventory and delivery adapter identities.

## Manual commands

The current safe command is:

```bash
./scripts/startup.sh serve --interval 60
```

It is only a liveness process today. Do not describe it as Fable supervision until workstreams 1–4 have evidence.

## Stop and rollback

- Stop immediately on absent/mutable unread semantics, unknown Fable session behavior, empty context, invalid JSON, timeout, or ambiguous delivery.
- Mark an affected task `HUMAN_REVIEW_NEEDED`; never retry a possibly delivered reply.
- Return to heartbeat-only mode with `Ctrl-C` (foreground) or stop the service/session; retain SQLite evidence and logs.
