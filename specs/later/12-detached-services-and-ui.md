# Later: detached terminals, services, and richer UI

## Why this is later

The simple controller inherits the operator's terminal. Making an interactive
provider survive terminal closure or run as a user service requires explicit
PTY ownership, attach/detach behavior, output buffering, and authentication
decisions. That is a different feature, not a small controller-loop change.

## Detached session scope

A future implementation must define:

- which process owns the pseudo-terminal;
- how an operator attaches and detaches;
- how terminal resize, input, and output buffering work;
- what happens after SSH disconnect;
- how secrets and provider login prompts are handled; and
- how old output is bounded and redacted.

Do not invent this by redirecting an interactive CLI to `/dev/null`.

## Service scope

Systemd user units remain disabled by default. Until detachable provider I/O is
implemented, a service may manage a headless controller or a future scheduler,
but it must not claim to provide a usable interactive provider session.

Installation may render reviewed units, but it must never enable them
implicitly.

## Richer UI scope

The simple text/JSON status projection is the source contract. A later TUI or
web dashboard consumes that projection and may add:

- activity history;
- health alerts;
- human-review details;
- attach/detach controls; and
- reviewed lifecycle actions.

It must not create a second scheduler or bypass the controller.

## Acceptance criteria

- an interactive provider survives detach and can be reattached;
- output remains bounded and terminal resize works;
- service and manual starts cannot own the same session concurrently;
- units install disabled; and
- UI actions use the controller's validated lifecycle interface.
