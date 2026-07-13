# Human-review handoff contract

When Fable has reached a real human boundary, it should make one final request
to the supervised Codex session before recording `HUMAN_REVIEW_NEEDED`.

Fable may choose its own wording based on the task and the reason for the
escalation. It does **not** need to send one fixed, canned message. Its
instruction must, however, require the underlying agent to respond using the
handoff format below, with every heading present and in this order.

## Required response format

```md
## Human review handoff

### Status
<One or two sentences describing the current state of the task.>

### Completed
- <Concrete completed item, including evidence where useful.>

### Blocked / unresolved
- <What cannot safely be completed and why.>

### Human decision needed
- <The exact decision, approval, information, access, or action required.>

### Relevant context
- Files: <paths, or `None`>
- Commands / checks: <commands run and their results, or `None`>
- External systems / links: <names and links, or `None`>

### Recommended next step
<The first action to take after the human supplies what is needed.>
```

Use `None` when a section has no applicable content. Do not omit headings,
replace them with synonyms, or include an open-ended request for more
transcript. The content should be specific enough for a returning human to
understand the task without reconstructing the conversation.

## Supervisor instruction requirements

When asking for the handoff, Fable’s instruction should make these points
clear:

1. This is the last automated turn because the task requires human review.
2. The agent must return only the required Markdown handoff format.
3. It must state the precise boundary that triggered escalation, not merely say
   that it is blocked.
4. It must report facts and completed verification accurately; it must not
   claim work it did not do.
5. It must recommend the smallest useful action for the human to take next.

After receiving a valid handoff, the supervisor stores the terminal
`HUMAN_REVIEW_NEEDED` marker. The human can use the consistent session summary
alongside the review queue, resolve the boundary, then explicitly re-enroll the
task with `reset-human-review`.
