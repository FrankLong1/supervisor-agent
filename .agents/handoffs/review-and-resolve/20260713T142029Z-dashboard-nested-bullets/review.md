# Dashboard review

Target: nested three-section review cards in `src/codex_supervisor/console.py`.

Independent reviewer status: unavailable. The configured Claude review backend is not logged in, as established during this dashboard iteration. This is an access follow-up, not a clean reviewer result.

Direct render inspection confirmed the user-facing contract: each card begins with “Task” and its title, has three parent bullets, and each parent has no more than four nested bullets. No task IDs or code markup are rendered.
