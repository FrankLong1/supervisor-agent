# Supervisor dashboard UX plan

> **Historical document.** Its useful UI ideas are retained, but current status
> ownership, attention semantics, and mutation boundaries are defined by
> [operator UI and alerts](../specs/10-operator-ui-and-alerts.md).

## Executive Summary

- **Goal:** Make the localhost supervisor page understandable in seconds: what Fable is doing, what needs the human, and what just changed.
- **Steps:**
  - Establish a three-section information hierarchy and status language.
  - Redesign row density, visual severity, and task detail presentation.
  - Verify the page against real local supervisor state at desktop and narrow widths.

## Loop Continuation Contract

- **Continue while:** A read-only UI improvement can be implemented and verified from local state without changing task decisions, replies, or marker state.
- **End condition:** The dashboard makes active work, delivery state, and human-review items scannable with real-state evidence and passing tests.
- **Manual human inputs:** Approval is needed only for controls that mutate tasks, expose the page remotely, or add notifications.
- **Human-gate impact:** Those features are out of scope and do not block this visual refresh.
- **Return timing:** After the read-only visual implementation and local browser verification.
- **Safe default:** Keep the page loopback-only, read-only, and show unknown/unavailable state rather than inventing it.
- **Retry/wait bounds:** One repair pass for rendering or state-projection defects; stop on any implication that the page needs task mutation.

## Goal

Turn the current table-heavy queue into a calm operational dashboard that answers three questions:

1. What is Fable working on now?
2. What needs my attention and why?
3. Did the supervisor itself remain healthy?

## Success criteria

### Evidence that proves completion

- The initial viewport makes each of the three answers visible without scrolling on a typical desktop screen.
- Active work, pending delivery, and human review use distinct text labels and visual treatments; color never carries meaning alone.
- Long Fable reasons are readable on demand without making every row overwhelming.
- Narrow-window rendering remains usable, and the page remains loopback-only/read-only.

## Scope

### In scope

- A compact top health strip: supervisor running state, last successful cycle, Fable session presence, and active/human-review counts.
- Three ordered sections: **In progress**, **Needs you**, and **Recent outcomes**.
- Task cards/rows with title first, status badge second, concise reason preview, timestamp, and copyable task ID.
- Expand/collapse for full Fable rationale, plus empty and unavailable states.
- Local CSS only, system fonts, responsive layout, and accessible contrast/focus states.

### Out of scope

- Task mutation buttons, reply editing, reset controls, remote access, login/auth, notifications, or external assets.
- A claimed desktop deep-link until Codex publishes a supported URI/API for opening a thread.

## Approach

### 1. Establish visual hierarchy

- Put a plain-language health line at the top: “Supervisor running”, “Last cycle”, and counts.
- Use **In progress** for active Codex threads and delivery-confirmation states.
- Use **Needs you** for `HUMAN_REVIEW_NEEDED`, sorted newest first.
- Put historical/confirmed work below the fold rather than mixing it with action items.

### 2. Make reasons scannable

- Derive a one-sentence preview from each stored reason, with a “Show full reason” disclosure for the preserved text.
- Display a semantic badge such as `working`, `awaiting confirmation`, `needs review`, or `supervisor unavailable`.
- Keep task ID secondary and copyable; title is primary.

### 3. Improve calmness and accessibility

- Use a restrained neutral base, one accent for active work, and a high-contrast attention treatment for human review.
- Provide icons only as redundant cues alongside text labels.
- Ensure keyboard-visible disclosure controls, readable line length, semantic headings, and a small-screen stacked card layout.

## Interfaces and state

- **Inputs:** Existing read-only SQLite human-review rows, delivery claims, heartbeat, and read-only app-server inventory.
- **Output:** One loopback-only HTML page refreshing every 15 seconds.
- **Privacy:** No raw task transcript, Fable prompt, session ID, credentials, or unsupported deep-link leaves the page.

## Verification

- Unit-test status classification, reason preview truncation, empty states, and HTML escaping.
- Verify the live page against the current supervisor backlog at `http://127.0.0.1:8765/`.
- Check desktop and narrow viewport rendering manually; confirm no mutation endpoint or external asset request exists.

## Rollout

- Implement behind the existing `./scripts/open-backlog.sh` command.
- Keep the current table layout available only until the redesigned page is locally verified.
- Roll back by restoring the static read-only table; retain all SQLite state unchanged.
