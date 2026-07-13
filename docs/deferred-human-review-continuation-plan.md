# Deferred plan: human-review continuation

## Decision for now

Keep `HUMAN_REVIEW_NEEDED` terminal. The existing dashboard/review queue is
the operator’s handoff surface: it shows the affected task, why Fable stopped,
and the task ID. A human can inspect the task, make the needed decision, and
explicitly re-enroll it with `reset-human-review`.

Do not add an automated continuation path in this phase.

The handoff contract is documented in
[human-review-handoff.md](human-review-handoff.md). It may be used later to
ask the supervised agent for a consistent in-thread summary, but it does not
change the terminal behavior today.

## Why this is deferred

A handoff response from the supervised agent changes the task just as a human
message would. The supervisor’s current idle/unread inventory does not prove
who authored a later change. Clearing a terminal marker merely because a task
changed could restart Fable on its own handoff response, which defeats the
human-review boundary.

The explicit reset is simple, visible, and safe. It also aligns with the
current guarantee that no classifier or agent can clear a terminal marker.

## Future design, if automatic resumption becomes necessary

Only pursue this after the Codex app-server exposes a stable, verified source
of turn provenance.

1. Add a terminal handoff state that is written **before** a final handoff
   instruction is delivered to the supervised task.
2. Record the delivery/turn identity and preserve the original review reason.
3. Ignore the supervised agent’s handoff response for scheduling purposes; it
   must never clear or weaken the terminal state.
4. Watch only for a later turn that the app server explicitly identifies as
   human-authored, and verify that it is newer than the recorded handoff turn.
5. On that verified human turn, clear the marker and schedule Fable against
   the human’s new request—not against the prior agent handoff.
6. Add canary coverage for author identity, ordering, delivery ambiguity,
   restarts, and the case where a human responds while a handoff is still
   settling.

If the app-server cannot provide reliable author provenance, retain explicit
human re-enrollment permanently rather than guessing from unread state or
message text.

## Acceptance criteria for a future implementation

- A terminal task is never reprocessed because its own agent completed the
  handoff.
- A human can still resume the task through the dashboard/CLI at any time.
- Automatic resumption occurs only after a verifiably human-authored message.
- Transport or provenance uncertainty remains terminal and visible in the
  dashboard.
