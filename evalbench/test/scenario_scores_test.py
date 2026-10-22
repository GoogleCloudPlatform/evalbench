"""Unit tests for the per-scenario scores attached to the response summary."""

import json
import unittest

import numpy as np
import pandas as pd

from reporting.analyzer import (
    SCENARIO_LOG_MAX_CHARS,
    fit_scenario_scores,
    scenario_scores,
)


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

    def test_logs_only_attached_to_judged_rows_without_full_marks(self):
        df = pd.DataFrame([
            {"id": "s1", "comparator": "goal_completion", "score": 100,
             "comparison_logs": "PASS Reasoning: fine."},
            {"id": "s2", "comparator": "goal_completion", "score": 0,
             "comparison_logs": "FAIL Reasoning: tool call hit a rate limit."},
            {"id": "s2", "comparator": "turn_count", "score": 1,
             "comparison_logs": "Agent took 1 turns."},
            {"id": "s2", "comparator": "end_to_end_latency", "score": 5013,
             "comparison_logs": "End-to-End latency was 5013.0 ms."},
        ])
        passed, failed, turns, latency = scenario_scores(df)
        self.assertNotIn("comparison_logs", passed)
        self.assertEqual(
            failed["comparison_logs"], "FAIL Reasoning: tool call hit a rate limit."
        )
        # Measured metrics never score 100; their one-liners only repeat the score.
        self.assertEqual(turns, {"id": "s2", "comparator": "turn_count", "score": 1})
        self.assertEqual(
            latency, {"id": "s2", "comparator": "end_to_end_latency", "score": 5013}
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


def _entries(num_scenarios, failing_ids=(), log="FAIL Reasoning: " + "x" * 200):
    """A judged + a measured row per scenario, failures carrying logs."""
    entries = []
    for i in range(num_scenarios):
        scenario_id = f"scenario-{i:05d}"
        judged = {"id": scenario_id, "comparator": "goal_completion", "score": 100}
        if scenario_id in failing_ids:
            judged.update(score=0, comparison_logs=log)
        entries.append(judged)
        entries.append({"id": scenario_id, "comparator": "turn_count", "score": 2})
    return entries


class TestFitScenarioScores(unittest.TestCase):

    def test_small_list_is_returned_unchanged(self):
        entries = _entries(3, failing_ids={"scenario-00001"})
        fitted, truncated = fit_scenario_scores(entries)
        self.assertIs(fitted, entries)
        self.assertFalse(truncated)

    def test_over_budget_keeps_only_failures_with_their_logs(self):
        entries = _entries(1000, failing_ids={"scenario-00007", "scenario-00042"})
        budget = len(json.dumps(entries)) - 1
        fitted, truncated = fit_scenario_scores(entries, max_bytes=budget)
        self.assertTrue(truncated)
        self.assertEqual([e["id"] for e in fitted], ["scenario-00007", "scenario-00042"])
        self.assertTrue(all("comparison_logs" in e for e in fitted))
        self.assertTrue(all(e["comparator"] == "goal_completion" for e in fitted))

    def test_too_many_failures_drop_logs_then_truncate(self):
        failing = {f"scenario-{i:05d}" for i in range(500)}
        entries = _entries(500, failing_ids=failing)
        failures_without_logs = [
            {"id": e["id"], "comparator": e["comparator"], "score": e["score"]}
            for e in entries if e["comparator"] == "goal_completion"
        ]
        slim_size = len(json.dumps(failures_without_logs))

        fitted, truncated = fit_scenario_scores(entries, max_bytes=slim_size)
        self.assertTrue(truncated)
        self.assertEqual(fitted, failures_without_logs)

        fitted, truncated = fit_scenario_scores(entries, max_bytes=slim_size // 2)
        self.assertTrue(truncated)
        self.assertLess(len(fitted), len(failures_without_logs))
        self.assertGreater(len(fitted), 0)
        self.assertEqual(fitted, failures_without_logs[:len(fitted)])
        self.assertLessEqual(len(json.dumps(fitted)), slim_size // 2)

    def test_budget_smaller_than_one_entry_returns_empty_list(self):
        entries = _entries(2, failing_ids={"scenario-00000"})
        fitted, truncated = fit_scenario_scores(entries, max_bytes=5)
        self.assertEqual(fitted, [])
        self.assertTrue(truncated)


if __name__ == "__main__":
    unittest.main()
