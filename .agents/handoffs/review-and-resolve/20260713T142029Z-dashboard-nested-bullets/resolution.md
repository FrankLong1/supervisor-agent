# Resolution

Reviewer access: follow-up. The missing Claude login does not affect this local read-only dashboard change.

Implemented a Task header and a bounded nested-bullet presentation. The three parent labels remain Status, What Fable established, and Your next step. Supporting sentences are capped at four per section and 240 characters each, with an ellipsis for clipped display. The full unmodified reason remains durable in SQLite.

Verification: 32 tests pass; `git diff --check` passes; the console was restarted; and HTTP-rendered output contains the Task header, nested lists, and no task ID markup. The live worker remains stopped.
