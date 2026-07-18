# Plan 09: detached interactive sessions

## Outcome

Allow `supervisor codex` and `supervisor claude` to survive terminal closure
and be reattached later.

This is independent of unread automation. The background worker does not need
an interactive terminal, and this feature must not be used as a shortcut for
plan 08.

## User-visible contract

```text
supervisor codex --detach
supervisor claude --detach
supervisor attach SESSION_ID
supervisor detach SESSION_ID
```

## Required design

- One controller owns a pseudo-terminal and provider process group.
- Attach/detach transfers terminal input without changing controller identity.
- Output is durably bounded, timestamped, and redacted where necessary.
- Terminal resize is forwarded.
- SSH disconnect detaches instead of killing the provider.
- Provider login prompts are visible only to the attached operator.

Do not implement detach by redirecting an interactive CLI to `/dev/null` or by
allowing multiple attachers to write concurrently.

## Verification

- Attach, resize, detach, SSH-disconnect simulation, and reattach preserve the
  same provider process.
- One-writer/many-reader rules are enforced.
- Buffer limits and secret redaction are tested.
- `stop` still reaches the controller and reaps the child.

## Done when

An interactive provider survives a lost terminal, can be safely reattached,
and retains the simple controller's identity and shutdown guarantees.
