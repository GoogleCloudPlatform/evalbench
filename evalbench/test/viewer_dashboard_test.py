import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

# Allow running unit tests in core test environments where mesop is not installed
sys.modules.setdefault("mesop", MagicMock())

# Add viewer directory and root to sys.path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../viewer"))
)
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
)

from viewer import dashboard


class TestFormatCount(unittest.TestCase):

    def test_formats_counts(self):
        for value, expected in [(1.667, "1.67"), (3.0, "3"), (3, "3"),
                                ("N/A", "N/A")]:
            with self.subTest(value=value):
                self.assertEqual(expected, dashboard._format_count(value))


class TestDashboardGoalCompletionFallback(unittest.TestCase):
    """summary.csv has no goal_completion row, so the dashboard computes it
    from scores.csv."""

    def setUp(self):
        self.results_dir = tempfile.mkdtemp()
        pd.DataFrame([{"metric_name": "trajectory_matcher",
                       "correct_results_count": 1,
                       "total_results_count": 2}]).to_csv(
            os.path.join(self.results_dir, "summary.csv"), index=False)

    def card_texts(self, scores):
        pd.DataFrame(scores).to_csv(
            os.path.join(self.results_dir, "scores.csv"), index=False)
        with patch.object(dashboard, "me") as me:
            dashboard.dashboard_component(self.results_dir)
        return [c.args[0] for c in me.text.call_args_list if c.args]

    def test_partial_and_non_numeric_scores(self):
        """A non-numeric score counts as 0, as in the analyzer."""
        texts = self.card_texts([
            {"comparator": "goal_completion", "score": 100.0},
            {"comparator": "goal_completion", "score": 50.0},
            {"comparator": "goal_completion", "score": "N/A"},
        ])

        self.assertIn("goal_completion", texts)
        self.assertIn("1.50/3", texts)

    def test_a_prompt_counts_its_weakest_trial(self):
        texts = self.card_texts([
            {"comparator": "goal_completion", "prompt_id": "p1", "score": 100.0},
            {"comparator": "goal_completion", "prompt_id": "p1", "score": 50.0},
            {"comparator": "goal_completion", "prompt_id": "p2", "score": 100.0},
        ])

        self.assertIn("1.50/2", texts)


if __name__ == "__main__":
    unittest.main()
