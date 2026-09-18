import os
import sys
import unittest
import pandas as pd

# Add viewer directory and root to sys.path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../viewer"))
)
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
)

from viewer.conversations import _get_conversation_status


class TestViewerConversationsStatus(unittest.TestCase):

    def test_neutral_when_scores_missing_or_empty(self):
        self.assertEqual(_get_conversation_status(None, "eval_1"), ("neutral", []))
        empty_df = pd.DataFrame(columns=["id", "comparator", "score", "unit"])
        self.assertEqual(
            _get_conversation_status(empty_df, "eval_1"), ("neutral", [])
        )

    def test_neutral_when_only_counter_metrics_present(self):
        df = pd.DataFrame(
            [
                {"id": "eval_1", "comparator": "turn_count", "score": 3, "unit": "turns"},
                {"id": "eval_1", "comparator": "agent_steps", "score": 5, "unit": "steps"},
                {"id": "eval_1", "comparator": "end_to_end_latency", "score": 1250, "unit": "ms"},
                {"id": "eval_1", "comparator": "token_consumption", "score": 450, "unit": "tokens"},
            ]
        )
        self.assertEqual(_get_conversation_status(df, "eval_1"), ("neutral", []))

    def test_red_when_goal_completion_below_100(self):
        df = pd.DataFrame(
            [
                {"id": "eval_1", "comparator": "goal_completion", "score": 0.0, "unit": "%"},
                {"id": "eval_1", "comparator": "trajectory_matcher", "score": 50.0, "unit": "%"},
                {"id": "eval_1", "comparator": "turn_count", "score": 4, "unit": "turns"},
            ]
        )
        status, failed = _get_conversation_status(df, "eval_1")
        self.assertEqual(status, "red")
        self.assertEqual(
            failed, ["goal_completion: 0%", "trajectory_matcher: 50%"]
        )

    def test_yellow_when_skills_or_other_metrics_below_100(self):
        df = pd.DataFrame(
            [
                {"id": "eval_2", "comparator": "goal_completion", "score": 100.0, "unit": ""},
                {"id": "eval_2", "comparator": "skills_trajectory", "score": 40.0, "unit": ""},
                {"id": "eval_2", "comparator": "skills_best_practices", "score": 60.0, "unit": ""},
                {"id": "eval_2", "comparator": "turn_count", "score": 2, "unit": ""},
                {"id": "eval_2", "comparator": "agent_steps", "score": 4, "unit": ""},
            ]
        )
        status, failed = _get_conversation_status(df, "eval_2")
        self.assertEqual(status, "yellow")
        self.assertEqual(
            failed, ["skills_trajectory: 40%", "skills_best_practices: 60%"]
        )

    def test_green_when_all_evaluative_metrics_100_with_low_counters(self):
        df = pd.DataFrame(
            [
                {"id": "eval_3", "comparator": "goal_completion", "score": 100.0, "unit": "%"},
                {"id": "eval_3", "comparator": "trajectory_matcher", "score": 100.0, "unit": "%"},
                {"id": "eval_3", "comparator": "skills_trajectory", "score": 100.0, "unit": ""},
                {"id": "eval_3", "comparator": "skills_best_practices", "score": 100.0, "unit": ""},
                {"id": "eval_3", "comparator": "turn_count", "score": 3, "unit": "turns"},
                {"id": "eval_3", "comparator": "agent_steps", "score": 6, "unit": "steps"},
                {"id": "eval_3", "comparator": "end_to_end_latency", "score": 850, "unit": "ms"},
                {"id": "eval_3", "comparator": "token_consumption", "score": 320, "unit": "tokens"},
            ]
        )
        status, failed = _get_conversation_status(df, "eval_3")
        self.assertEqual(status, "green")
        self.assertEqual(failed, [])

    def test_new_evaluative_scorer_included_by_default(self):
        df = pd.DataFrame(
            [
                {"id": "eval_4", "comparator": "goal_completion", "score": 100.0, "unit": ""},
                {"id": "eval_4", "comparator": "custom_new_scorer", "score": 75.0, "unit": ""},
            ]
        )
        status, failed = _get_conversation_status(df, "eval_4")
        self.assertEqual(status, "yellow")
        self.assertEqual(failed, ["custom_new_scorer: 75%"])


if __name__ == "__main__":
    unittest.main()
