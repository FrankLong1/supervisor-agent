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


if __name__ == "__main__":
    unittest.main()
