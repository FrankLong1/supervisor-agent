---
name: supervisor-skill-mode
description: Start or continue an explicitly requested Codex goal and supervise the work through verified completion. Use when the user invokes `$supervisor-skill-mode`, asks to enter the supervisor skill mode, asks to enter Goal Mode, or explicitly asks Codex to keep working autonomously on a concrete outcome. Do not use for ordinary one-turn requests, planning or brainstorming without execution, or when the outcome is too ambiguous to define safely.
---

# Supervisor Skill Mode

Treat explicit invocation of this skill as authorization to create a goal for
the requested work. Goal Mode preserves the task lifecycle; this skill supplies
the operating contract.

## Establish the Goal

1. Read applicable instructions and inspect enough local context to state the
   work accurately.
2. Call `get_goal` before creating a goal.
3. Handle the current goal state conservatively:
   - If no unfinished goal exists, call `create_goal`.
   - If the active goal covers the same outcome, continue it without creating
     another goal.
   - If an unrelated unfinished goal exists, do not replace it. Explain the
     conflict and let the user clear or finish it.
4. Write one concrete objective containing, when applicable:
   - the required outcome;
   - material constraints and authority boundaries; and
   - observable tests, measurements, or review criteria proving completion.
5. Preserve the user's intended scope. Do not turn implementation into a
   proposal, add speculative deliverables, or expand authority.
6. Set `token_budget` only when the user explicitly requested a numeric token
   budget.

After creating or finding the goal, begin execution in the same turn. Do not
stop merely to restate the objective or present a plan.

## Execute Autonomously

1. Inspect the relevant workspace, systems, and existing state before editing.
2. Make reasonable reversible assumptions when the answer is discoverable.
3. Carry the work through the complete normal workflow implied by the goal:
   investigate, implement, test, repair failures, publish or deploy when
   authorized, and verify the real result.
4. Use a plan when it helps organize substantial work, and update it as work
   completes. The plan is execution state, not a user approval gate.
5. Keep edits scoped, preserve unrelated user work, and follow repository and
   system safety boundaries.
6. Continue across goal turns while meaningful work remains. Treat follow-up
   user messages as steering for the active goal unless they clearly replace
   it.
7. Ask the user only when required information or authority cannot be
   discovered safely, the target is materially ambiguous, or proceeding would
   cross a non-delegable boundary.

Do not stop at analysis, a proposal, a diff, a preview, a passing narrow test,
or a "ready to" handoff when the goal requires a completed and verified result.

## Close the Goal

Before changing goal status, call `get_goal` and compare the current result
with the objective's completion criteria.

- Call `update_goal` with `complete` only when the outcome is achieved, its
  required checks pass, and no required work remains.
- Call `update_goal` with `blocked` only when the same genuine blocker has
  prevented meaningful progress for at least three consecutive goal turns and
  user input or external state is required.
- Do not mark a goal complete because the budget is low, a partial milestone
  landed, or additional required work is inconvenient.
- Do not imitate pause, resume, clearing, or budget controls; those belong to
  the user and Goal Mode lifecycle.

Return a concise closeout stating the result, verification performed, and any
remaining limitation. When a budgeted goal completes, include the final token
usage returned by `update_goal`.
