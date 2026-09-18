"""Unit tests for SkillsBestPractices skill-root resolution."""

import json
import os
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorers.skillsbestpractices import SkillsBestPractices

SKILL = "cloud-sql-postgres-admin"

# Where each harness installs skills, relative to its own fake_home.
HARNESS_LAYOUTS = {
    "codex": os.path.join(".codex", "skills"),
    "gemini": os.path.join(".gemini", "skills"),
    "agy": os.path.join(".gemini", "config", "plugins", "cloud-sql", "skills"),
    "claude": os.path.join(
        ".claude", "plugins", "marketplaces", "cloud-sql", "skills"),
}


def _make_scorer(skills_dir=""):
    """Builds a scorer without the LLM wiring __init__ would set up."""
    scorer = object.__new__(SkillsBestPractices)
    scorer.skills_dir = skills_dir
    return scorer


def _write_skill(root, skill_name=SKILL):
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

    def test_resolves_every_harness_layout(self):
        for harness, layout in HARNESS_LAYOUTS.items():
            with self.subTest(harness=harness):
                fake_home = os.path.join(self.tmp.name, harness)
                root = os.path.join(fake_home, layout)
                expected = _write_skill(root)
                scorer = _make_scorer()

                roots = scorer._resolve_skill_roots(fake_home)

                self.assertEqual(roots, [root])
                self.assertEqual(scorer._find_skill_md(SKILL, roots), expected)

    def test_never_falls_back_to_another_sandbox(self):
        """The skill is absent from the agy sandbox but present in a codex one
        beside it. Scoring must report it missing, not read the codex copy."""
        agy_home = os.path.join(self.tmp.name, "fake_home_agy")
        agy_root = os.path.join(agy_home, HARNESS_LAYOUTS["agy"])
        os.makedirs(agy_root, exist_ok=True)
        _write_skill(os.path.join(self.tmp.name, "fake_home_codex",
                                  HARNESS_LAYOUTS["codex"]))
        scorer = _make_scorer()

        roots = scorer._resolve_skill_roots(agy_home)

        self.assertEqual(roots, [agy_root])
        self.assertIsNone(scorer._find_skill_md(SKILL, roots))

    def test_explicit_skills_dir_wins(self):
        configured = os.path.join(self.tmp.name, "configured")
        _write_skill(configured)
        scorer = _make_scorer(skills_dir=configured)

        self.assertEqual(
            scorer._resolve_skill_roots(self.tmp.name), [configured])

    def test_missing_fake_home_yields_no_roots(self):
        self.assertEqual(_make_scorer()._resolve_skill_roots(None), [])


class SkillsBestPracticesDedupeTest(unittest.TestCase):

    def test_repeated_skill_is_graded_once(self):
        """A skill lands in accumulated_skills once per turn it was used in.
        Grading each entry would spend an extra LLM call and weight that skill
        twice in the mean."""
        scorer = _make_scorer(skills_dir="/unused")
        graded = []
        scores = {"admin": 60.0, "lifecycle": 90.0}

        def fake_score(skill_name, skills_roots):
            graded.append(skill_name)
            return scores[skill_name], "stub"

        scorer._score_skill = fake_score
        context = json.dumps(
            {"accumulated_skills": ["admin", "lifecycle", "admin"]})

        score, _ = scorer.compare(
            None, None, None, None, None, None, None, None, context, None)

        self.assertEqual(graded, ["admin", "lifecycle"])
        # Mean of the two distinct skills, not (60 + 90 + 60) / 3.
        self.assertEqual(score, 75.0)


if __name__ == "__main__":
    unittest.main()
