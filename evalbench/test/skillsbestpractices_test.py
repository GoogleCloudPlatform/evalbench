"""Unit tests for SkillsBestPractices skill-root resolution."""

import os
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorers.skillsbestpractices import SkillsBestPractices


def _make_scorer(skills_dir=""):
    """Builds a scorer without the LLM wiring __init__ would set up."""
    scorer = object.__new__(SkillsBestPractices)
    scorer.skills_dir = skills_dir
    return scorer


def _write_skill(root, skill_name):
    skill_dir = os.path.join(root, skill_name)
    os.makedirs(skill_dir, exist_ok=True)
    path = os.path.join(skill_dir, "SKILL.md")
    with open(path, "w") as f:
        f.write(f"---\nname: {skill_name}\n---\n")
    return path


class SkillsBestPracticesRootsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # An agy sandbox, plus a codex sandbox alongside it holding the same
        # skill name -- the layout that made agy runs score against codex.
        self.agy_home = os.path.join(self.tmp.name, "fake_home_agy")
        self.agy_root = os.path.join(
            self.agy_home, ".gemini", "config", "plugins",
            "cloud-sql-postgresql", "skills")
        self.codex_root = os.path.join(
            self.tmp.name, "fake_home_codex", ".codex", "skills")
        _write_skill(self.codex_root, "cloud-sql-postgres-admin")

    def test_resolves_agy_plugin_skills(self):
        expected = _write_skill(self.agy_root, "cloud-sql-postgres-admin")
        scorer = _make_scorer()

        roots = scorer._resolve_skill_roots(self.agy_home)
        found = scorer._find_skill_md("cloud-sql-postgres-admin", roots)

        self.assertEqual(roots, [self.agy_root])
        self.assertEqual(found, expected)

    def test_never_falls_back_to_another_sandbox(self):
        """The skill is absent from the agy sandbox but present in the codex
        one. Scoring must report it missing rather than read the codex copy."""
        os.makedirs(self.agy_root, exist_ok=True)
        scorer = _make_scorer()

        roots = scorer._resolve_skill_roots(self.agy_home)
        found = scorer._find_skill_md("cloud-sql-postgres-admin", roots)

        self.assertIsNone(found)

    def test_resolves_claude_marketplace_skills(self):
        claude_home = os.path.join(self.tmp.name, "fake_home_claude")
        root = os.path.join(claude_home, ".claude", "plugins", "marketplaces",
                            "cloud-sql", "skills")
        expected = _write_skill(root, "cloud-sql-postgres-admin")
        scorer = _make_scorer()

        roots = scorer._resolve_skill_roots(claude_home)

        self.assertEqual(roots, [root])
        self.assertEqual(
            scorer._find_skill_md("cloud-sql-postgres-admin", roots), expected)

    def test_explicit_skills_dir_wins(self):
        scorer = _make_scorer(skills_dir=self.codex_root)
        self.assertEqual(scorer._resolve_skill_roots(self.agy_home),
                         [self.codex_root])

    def test_missing_fake_home_yields_no_roots(self):
        self.assertEqual(_make_scorer()._resolve_skill_roots(None), [])


if __name__ == "__main__":
    unittest.main()
