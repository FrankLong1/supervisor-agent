# Resolution: Fable supervisor policy

## Authoritative artifact

`src/codex_supervisor/supervisor_prompt.md` is the authoritative policy. The
runtime loads this file and supplies it on both new and resumed Fable calls.

## Acting-model evaluation

There were no parseable independent-review findings to classify. The empty
review output is recorded in `review.md` as a review limitation rather than
being treated as approval.

## Bounded policy change applied

The prompt now:

- defaults to one concrete, safe, high-leverage next action;
- requires the reply to state an intended result or check;
- treats completion language as evidence rather than an automatic stop;
- prohibits manufactured polish, repeated checks, scope reopening, and
  performative handoffs; and
- reserves human review for genuine authority, context, or risk boundaries.

## Verification and next step

`uv run --with pytest pytest -q` passed: 28 tests. The worker must be restarted
once after this closeout so the running process imports the revised file-backed
policy; subsequent prompt edits apply to the next Fable invocation without a
further code change.
