"""Unit tests for SkillsTrajectoryMatcher."""

import json
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorers.skillstrajectorymatcher import SkillsTrajectoryMatcher


def _make_context(expected_skills, accumulated_skills):
    return json.dumps({
        "scenario": {"expected_skills": expected_skills},
        "accumulated_skills": accumulated_skills,
    })


def _compare(matcher, expected_skills, accumulated_skills):
    context = _make_context(expected_skills, accumulated_skills)
    return matcher.compare(
        nl_prompt=None,
        golden_query=None,
        query_type=None,
        golden_execution_result=None,
        golden_eval_result=None,
        golden_error=None,
        generated_query=None,
        generated_execution_result=None,
        generated_eval_result=context,
        generated_error=None,
    )


class SkillsTrajectoryMatcherTest(unittest.TestCase):

    def test_both_empty_returns_full_score(self):
        matcher = SkillsTrajectoryMatcher({})
        score, explanation = _compare(matcher, [], [])
        self.assertEqual(score, 100.0)
        self.assertIn("Both expected and actual skill lists are empty", explanation)

    def test_empty_expected_skills_with_extra_skills_when_allow_extra_false(self):
        matcher = SkillsTrajectoryMatcher({"allow_extra_skills": False})
        score, explanation = _compare(matcher, [], ["dataform_bigquery"])
        self.assertEqual(score, 0.0)
        self.assertIn("Jaccard Similarity: 0.00", explanation)

    def test_empty_expected_skills_with_extra_skills_when_allow_extra_true(self):
        matcher = SkillsTrajectoryMatcher({"allow_extra_skills": True})
        score, explanation = _compare(matcher, [], ["dataform_bigquery"])
        self.assertEqual(score, 100.0)
        self.assertIn("extra skills are allowed", explanation)

    def test_default_uses_jaccard_similarity(self):
        matcher = SkillsTrajectoryMatcher({})
        expected = ["dataform_bigquery"]
        actual = ["dataform_bigquery", "gcp_pipeline_orchestration"]

        score, explanation = _compare(matcher, expected, actual)
        self.assertEqual(score, 50.0)
        self.assertIn("Jaccard Similarity", explanation)

    def test_allow_extra_skills_enabled(self):
        matcher = SkillsTrajectoryMatcher({"allow_extra_skills": True})
        expected = ["dataform_bigquery"]
        actual = ["dataform_bigquery", "gcp_pipeline_orchestration"]

        score, explanation = _compare(matcher, expected, actual)
        self.assertEqual(score, 100.0)
        self.assertIn("allow_extra_skills=True", explanation)

    def test_partial_coverage(self):
        matcher = SkillsTrajectoryMatcher({"allow_extra_skills": True})
        expected = ["dataform_bigquery", "dbt_bigquery"]
        actual = ["dataform_bigquery"]

        score, explanation = _compare(matcher, expected, actual)
        self.assertEqual(score, 50.0)

    def test_enforce_order(self):
        matcher = SkillsTrajectoryMatcher({"enforce_order": True})
        expected = ["skill_a", "skill_b"]
        actual = ["skill_b", "skill_a"]

        score, explanation = _compare(matcher, expected, actual)
        self.assertLess(score, 100.0)
        self.assertIn("Sequence Alignment", explanation)

    def test_invalid_config_combination_raises_error(self):
        with self.assertRaises(ValueError):
            SkillsTrajectoryMatcher({"enforce_order": True, "allow_extra_skills": True})


if __name__ == "__main__":
    unittest.main()
