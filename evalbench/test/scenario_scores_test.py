"""Unit tests for the per-scenario scores attached to the response summary."""

import json
import unittest

import numpy as np
import pandas as pd

from reporting.analyzer import SCENARIO_LOG_MAX_CHARS, scenario_scores


class TestScenarioScores(unittest.TestCase):

    def test_empty_or_missing_id_column_returns_empty_list(self):
        self.assertEqual(scenario_scores(None), [])
        self.assertEqual(scenario_scores(pd.DataFrame()), [])
        self.assertEqual(
            scenario_scores(pd.DataFrame([{"comparator": "goal_completion", "score": 0}])),
            [],
        )

    def test_one_entry_per_row_in_order_with_id_comparator_score(self):
        df = pd.DataFrame([
            {"id": "s1", "comparator": "trajectory_matcher", "score": 100},
            {"id": "s1", "comparator": "goal_completion", "score": 100},
            {"id": "s2", "comparator": "trajectory_matcher", "score": 100},
            {"id": "s2", "comparator": "goal_completion", "score": 0},
            {"id": "s2", "comparator": "turn_count", "score": 3},
        ])
        entries = scenario_scores(df)
        self.assertEqual(
            [(e["id"], e["comparator"], e["score"]) for e in entries],
            [
                ("s1", "trajectory_matcher", 100),
                ("s1", "goal_completion", 100),
                ("s2", "trajectory_matcher", 100),
                ("s2", "goal_completion", 0),
                ("s2", "turn_count", 3),
            ],
        )

    def test_logs_only_attached_to_rows_without_full_marks(self):
        df = pd.DataFrame([
            {"id": "s1", "comparator": "goal_completion", "score": 100,
             "comparison_logs": "PASS Reasoning: fine."},
            {"id": "s2", "comparator": "goal_completion", "score": 0,
             "comparison_logs": "FAIL Reasoning: tool call hit a rate limit."},
        ])
        passed, failed = scenario_scores(df)
        self.assertNotIn("comparison_logs", passed)
        self.assertEqual(
            failed["comparison_logs"], "FAIL Reasoning: tool call hit a rate limit."
        )

    def test_errors_are_attached_whenever_set(self):
        df = pd.DataFrame([
            {"id": "s1", "comparator": "goal_completion", "score": 100,
             "comparison_error": None, "generated_error": ""},
            {"id": "s2", "comparator": "goal_completion", "score": None,
             "comparison_error": "judge timed out",
             "generated_error": "agent exited 1"},
        ])
        clean, errored = scenario_scores(df)
        self.assertNotIn("comparison_error", clean)
        self.assertNotIn("generated_error", clean)
        self.assertIsNone(errored["score"])
        self.assertEqual(errored["comparison_error"], "judge timed out")
        self.assertEqual(errored["generated_error"], "agent exited 1")

    def test_long_logs_are_truncated_with_a_marker(self):
        logs = "x" * (SCENARIO_LOG_MAX_CHARS + 25)
        df = pd.DataFrame([
            {"id": "s1", "comparator": "goal_completion", "score": 0,
             "comparison_logs": logs},
        ])
        (entry,) = scenario_scores(df)
        self.assertTrue(entry["comparison_logs"].startswith("x" * SCENARIO_LOG_MAX_CHARS))
        self.assertTrue(entry["comparison_logs"].endswith("... [truncated 25 chars]"))

        (entry,) = scenario_scores(df, max_log_chars=10)
        self.assertEqual(
            entry["comparison_logs"],
            "x" * 10 + f"... [truncated {SCENARIO_LOG_MAX_CHARS + 15} chars]",
        )

    def test_rows_without_a_scenario_id_are_skipped(self):
        df = pd.DataFrame([
            {"id": None, "comparator": "goal_completion", "score": 0},
            {"id": "", "comparator": "goal_completion", "score": 0},
            {"id": "s1", "comparator": "goal_completion", "score": 0},
        ])
        self.assertEqual([e["id"] for e in scenario_scores(df)], ["s1"])

    def test_output_is_json_serialisable_with_numpy_and_nan_values(self):
        # A column mixing binary and measured scores is upcast to float64 by
        # pandas; whole-number scores must still come out as JSON integers.
        df = pd.DataFrame([
            {"id": "s1", "comparator": "goal_completion",
             "score": np.int64(100), "comparison_logs": np.nan},
            {"id": "s2", "comparator": "end_to_end_latency",
             "score": np.float64(1234.5), "comparison_logs": np.nan,
             "comparison_error": np.nan},
            {"id": "s3", "comparator": "goal_completion",
             "score": np.nan, "comparison_error": "judge timed out"},
        ])
        self.assertEqual(str(df["score"].dtype), "float64")
        entries = scenario_scores(df)
        decoded = json.loads(json.dumps(entries))
        self.assertEqual(decoded[0], {"id": "s1", "comparator": "goal_completion", "score": 100})
        self.assertEqual(
            decoded[1], {"id": "s2", "comparator": "end_to_end_latency", "score": 1234.5}
        )
        self.assertEqual(
            decoded[2],
            {"id": "s3", "comparator": "goal_completion", "score": None,
             "comparison_error": "judge timed out"},
        )
        self.assertIsInstance(entries[0]["score"], int)
        self.assertIsInstance(entries[1]["score"], float)
        self.assertIsNone(entries[2]["score"])


if __name__ == "__main__":
    unittest.main()
