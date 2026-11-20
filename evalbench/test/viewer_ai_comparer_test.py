"""Tests for the prompt size of the viewer AI comparison."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

# Add viewer directory and root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../viewer")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import ai_comparer


class ComparePromptBoundsTest(unittest.TestCase):
    """The comparison prompt must stay small when run files hold huge cells."""

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.results = temp_dir.name
        for run in ("run-1", "run-2"):
            run_dir = os.path.join(self.results, run)
            os.makedirs(run_dir)
            pd.DataFrame({
                "id": list(range(8)),
                "conversation_history": ["h" * 500_000] * 8,
            }).to_csv(os.path.join(run_dir, "evals.csv"), index=False)
            pd.DataFrame({
                "comparator": ["goal_completion"] * 8,
                "comparison_logs": ["x" * 500_000] + ["{}"] * 7,
            }).to_csv(os.path.join(run_dir, "scores.csv"), index=False)

    def _compare(self):
        generator = MagicMock()
        generator.vertex_model = "gemini-2.5-flash"
        generator.client.models.generate_content.return_value.text = "Same."
        env = {k: v for k, v in os.environ.items() if k != "GOOGLE_API_KEY"}
        env["RESULTS_DIR"] = self.results
        with patch.dict(os.environ, env, clear=True):
            with patch.object(
                ai_comparer, "get_summarizer", return_value=generator
            ):
                result = ai_comparer.compare_evals("run-1", "run-2")
        _, kwargs = generator.client.models.generate_content.call_args
        return result, kwargs["contents"]

    def test_prompt_is_bounded(self):
        result, prompt = self._compare()

        self.assertEqual(result, "Same.")
        # Each run file holds megabytes. The prompt holds both runs.
        self.assertLess(len(prompt), 600_000)
        self.assertIn("### Run 1: run-1", prompt)
        self.assertIn("### Run 2: run-2", prompt)
        self.assertIn("Scores:", prompt)


if __name__ == "__main__":
    unittest.main()
