# Dashboard review

Target: the rendered local Fable human-review dashboard in `src/codex_supervisor/console.py`.

The independent dashboard reviewer could not run because its Claude backend returned `Not logged in · Please run /login`. No independent findings are available; this is a reviewer-access blocker, not a clean review.

Rendered evidence was inspected directly: the page returned HTTP 200 after restart; the review cards place “Why Fable stopped” and a bold first sentence before the contextual detail; task IDs are absent; marked time is subdued metadata; and existing historical rows truthfully say that turns were not recorded.
