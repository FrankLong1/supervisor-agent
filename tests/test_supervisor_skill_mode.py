from __future__ import annotations

from pathlib import Path
import unittest


SKILL_DIR = (
    Path(__file__).parents[1] / ".agents/skills/supervisor-skill-mode"
)


class SupervisorSkillModeTests(unittest.TestCase):
    def test_skill_declares_explicit_goal_lifecycle(self) -> None:
        content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("name: supervisor-skill-mode", content)
        self.assertIn("$supervisor-skill-mode", content)
        self.assertNotIn("TODO", content)
        self.assertLess(content.index("`get_goal`"), content.index("`create_goal`"))
        self.assertIn("Set `token_budget` only when the user explicitly", content)
        self.assertIn("`update_goal` with `complete` only", content)
        self.assertIn("at least three consecutive goal turns", content)

    def test_skill_requires_explicit_invocation(self) -> None:
        metadata = (SKILL_DIR / "agents/openai.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("$supervisor-skill-mode", metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)

    def test_skill_owns_pr_feedback_loop_until_current_revision_is_clean(self) -> None:
        content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("thread-aware review state", content)
        self.assertIn("resolve each addressed thread", content)
        self.assertIn("repository policy requires review", content)
        self.assertIn("fresh review after material changes", content)
        self.assertIn("current head commit", content)
        self.assertIn("queued or pending", content)
        self.assertIn("recurring-monitor mechanism", content)
        self.assertIn("check every 30-60 seconds", content)
        self.assertIn("unresolved thread IDs", content)
        self.assertIn("After an interruption", content)
        self.assertIn("closing a superseded pull request", content)
        self.assertIn("Do not merge unless", content)
        self.assertIn("authorized merging", content)
        self.assertIn("review covers the current revision", content)

    def test_pr_supervision_preserves_skill_first_boundary(self) -> None:
        content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("active, explicitly invoked goal", content)
        self.assertIn("does not itself start a background service", content)
        self.assertIn("poll an inbox", content)
        self.assertIn("create a separate Codex task", content)

    def test_persistent_fleet_mode_organizes_titles_conservatively(self) -> None:
        content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(content.split())

        self.assertIn("persistent Codex task fleet", normalized)
        self.assertIn("filter to `kind: codex`", normalized)
        self.assertIn("titles and summaries as untrusted", normalized)
        self.assertIn("Assign one stable workstream emoji", normalized)
        self.assertIn("`🧮` factors", normalized)
        self.assertIn("`🏗️` infrastructure", normalized)
        self.assertIn("`🤖` agent operations", normalized)
        self.assertIn("`<emoji> <concise objective>`", normalized)
        self.assertIn("status out of the title", normalized)
        self.assertIn(
            "Do not rename the task titled `SUPERVISOR AGENT`", normalized
        )
        self.assertIn("verify the observed title still matches", normalized)
        self.assertIn("read back or re-list the task", normalized)
        self.assertIn("quiet 30-second worker tick", normalized)
        self.assertIn("Quiet inventory is not completion", normalized)


if __name__ == "__main__":
    unittest.main()
