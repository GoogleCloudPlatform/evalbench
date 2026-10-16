import os
import sys
import unittest

import pandas as pd

# protoc generates flat imports in eval_service_pb2_grpc (e.g. import
# eval_agent_pb2). Ensure evalproto is in sys.path so stubs resolve cleanly.
_PROTO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evalproto"
)
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

from eval_service import _build_item_scores  # noqa: E402
import reporting.analyzer as analyzer  # noqa: E402


class TestBuildItemScoresThroughAnalyzer(unittest.TestCase):
    """analyze_result casts id/error columns to the pandas "string" dtype,
    so missing values arrive as pd.NA. Build from its real output."""

    def test_pd_na_is_treated_as_blank_and_output_is_json(self):
        import json
        scores = [
            {"id": "1", "comparator": "exact_match", "score": 100,
             "generated_sql": "select 1", "generated_error": None,
             "golden_error": None, "comparison_error": None,
             "comparison_logs": None},
            {"id": "1", "comparator": "llmrater", "score": 100,
             "generated_sql": "select 1", "generated_error": None,
             "golden_error": None, "comparison_error": None,
             "comparison_logs": None},
            {"id": "2", "comparator": "exact_match", "score": 0,
             "generated_sql": "select x", "generated_error": "boom",
             "golden_error": None, "comparison_error": None,
             "comparison_logs": None},
            {"id": "2", "comparator": "llmrater", "score": 0,
             "generated_sql": "select x", "generated_error": "boom",
             "golden_error": None, "comparison_error": None,
             "comparison_logs": None},
        ]
        config = {"scorers": {"exact_match": {}, "llmrater": {}}}
        scores_df, _ = analyzer.analyze_result(
            scores, config, num_prompts=2, num_trials=1)
        # Precondition: the cast really happened.
        self.assertEqual(str(scores_df["generated_error"].dtype), "string")

        item_scores = _build_item_scores(scores_df, num_trials=1)
        self.assertEqual(item_scores["1"]["executable"], 1.0)
        self.assertFalse(item_scores["1"]["golden_failed"])
        self.assertNotIn("scorer_errors", item_scores["1"])
        self.assertEqual(item_scores["2"]["executable"], 0.0)
        self.assertNotIn("scorer_errors", item_scores["2"])
        # Must round-trip through json.dumps without NaN/NA leaking.
        text = json.dumps(item_scores)
        self.assertNotIn("NaN", text)
        self.assertEqual(json.loads(text), item_scores)


def _row(item_id, comparator, score, generated_error=None,
         golden_error=None, comparison_error=None):
    return {
        "id": item_id,
        "comparator": comparator,
        "score": score,
        "generated_error": generated_error,
        "golden_error": golden_error,
        "comparison_error": comparison_error,
    }


class TestBuildItemScores(unittest.TestCase):

    def test_collapses_comparator_rows_per_item(self):
        df = pd.DataFrame([
            _row("1", "exact_match", 100),
            _row("1", "llmrater", 100),
            _row("2", "exact_match", 0),
            _row("2", "llmrater", 100),
        ])
        scores = _build_item_scores(df, num_trials=1)
        self.assertEqual(set(scores), {"1", "2"})
        self.assertEqual(scores["1"]["exact_match"], 1.0)
        self.assertEqual(scores["1"]["llmrater"], 1.0)
        self.assertEqual(scores["2"]["exact_match"], 0.0)
        self.assertEqual(scores["2"]["llmrater"], 1.0)
        for entry in scores.values():
            self.assertEqual(entry["executable"], 1.0)
            self.assertFalse(entry["golden_failed"])
            self.assertNotIn("scorer_errors", entry)

    def test_executable_reflects_generated_error(self):
        df = pd.DataFrame([
            _row("1", "llmrater", 0, generated_error="syntax error"),
            _row("2", "llmrater", 100, generated_error=""),
            _row("3", "llmrater", 100, generated_error=float("nan")),
        ])
        scores = _build_item_scores(df, num_trials=1)
        self.assertEqual(scores["1"]["executable"], 0.0)
        self.assertEqual(scores["2"]["executable"], 1.0)
        self.assertEqual(scores["3"]["executable"], 1.0)

    def test_golden_failed_flags_unfair_zeros(self):
        df = pd.DataFrame([
            _row("1", "llmrater", 0, golden_error="504 Deadline Exceeded"),
            _row("2", "llmrater", 0, generated_error="bad sql"),
        ])
        scores = _build_item_scores(df, num_trials=1)
        self.assertTrue(scores["1"]["golden_failed"])
        self.assertEqual(scores["1"]["executable"], 1.0)
        self.assertFalse(scores["2"]["golden_failed"])
        self.assertEqual(scores["2"]["executable"], 0.0)

    def test_scorer_errors_listed_per_comparator(self):
        df = pd.DataFrame([
            _row("1", "exact_match", 100),
            _row("1", "llmrater", 0, comparison_error="RateLimit"),
        ])
        scores = _build_item_scores(df, num_trials=1)
        self.assertEqual(scores["1"]["scorer_errors"], ["llmrater"])
        self.assertEqual(scores["1"]["llmrater"], 0.0)

    def test_non_numeric_score_becomes_zero(self):
        df = pd.DataFrame([_row("1", "llmrater", None)])
        scores = _build_item_scores(df, num_trials=1)
        self.assertEqual(scores["1"]["llmrater"], 0.0)

    def test_skips_rows_without_id_or_comparator(self):
        df = pd.DataFrame([
            _row(None, "llmrater", 100),
            _row("1", None, 100),
            _row("1", "llmrater", 100),
        ])
        scores = _build_item_scores(df, num_trials=1)
        self.assertEqual(scores, {
            "1": {"executable": 1.0, "golden_failed": False,
                  "llmrater": 1.0},
        })

    def test_ids_are_stringified(self):
        df = pd.DataFrame([_row(400, "llmrater", 100)])
        scores = _build_item_scores(df, num_trials=1)
        self.assertIn("400", scores)

    def test_multi_trial_returns_empty(self):
        df = pd.DataFrame([
            _row("1_trial_0", "llmrater", 100),
            _row("1_trial_1", "llmrater", 0),
        ])
        self.assertEqual(_build_item_scores(df, num_trials=2), {})

    def test_empty_or_missing_columns_returns_empty(self):
        self.assertEqual(_build_item_scores(pd.DataFrame(), 1), {})
        self.assertEqual(_build_item_scores(None, 1), {})
        self.assertEqual(
            _build_item_scores(pd.DataFrame([{"score": 100}]), 1), {}
        )


if __name__ == "__main__":
    unittest.main()
