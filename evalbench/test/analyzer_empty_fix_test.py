"""Unit tests for empty evaluation handling and tuple/non-dict result sets."""

import unittest
from unittest.mock import MagicMock
import pandas as pd

from reporting.analyzer import analyze_one_metric, analyze_result
from scorers.setmatcher import SetMatcher
from scorers.llmrater import LLMRater


class TestAnalyzerEmptyEvaluation(unittest.TestCase):

    def test_empty_dataframe_does_not_raise_key_error(self):
        df_empty = pd.DataFrame()
        res = analyze_one_metric(df_empty, "llmrater", 100, num_prompts=10)
        self.assertEqual(res["metric_name"], "llmrater")
        self.assertEqual(res["correct_results_count"], 0)
        self.assertEqual(res["total_results_count"], 10)

    def test_dataframe_missing_score_column(self):
        df = pd.DataFrame([{"comparator": "llmrater", "prompt_id": "p1"}])
        res = analyze_one_metric(df, "llmrater", 100, num_prompts=5)
        self.assertEqual(res["correct_results_count"], 0)
        self.assertEqual(res["total_results_count"], 5)

    def test_analyze_result_with_empty_scores_list(self):
        scores_df, summary_df = analyze_result(
            [], {"scorers": {"llmrater": {}}}, num_prompts=10
        )
        self.assertEqual(len(summary_df), 2)  # llmrater and executable
        llmrater_summary = summary_df[summary_df["metric_name"] == "llmrater"].iloc[0]
        self.assertEqual(llmrater_summary["correct_results_count"], 0)
        self.assertEqual(llmrater_summary["total_results_count"], 10)

    def test_setmatcher_handles_empty_none_and_tuples(self):
        matcher = SetMatcher({})
        # Error with None
        score, err = matcher.compare(
            nl_prompt="q", golden_query="", query_type="dql",
            golden_execution_result=None, golden_eval_result="", golden_error="error",
            generated_query="", generated_execution_result=None, generated_eval_result="",
            generated_error="error",
        )
        self.assertEqual(score, 0)

        # Tuple rows instead of dicts
        score, err = matcher.compare(
            nl_prompt="q", golden_query="", query_type="dql",
            golden_execution_result=[("val1", "val2")], golden_eval_result="", golden_error=None,
            generated_query="", generated_execution_result=[("val1", "val2")], generated_eval_result="",
            generated_error=None,
        )
        self.assertEqual(score, 100)

    def test_llmrater_take_n_uniques_handles_none_and_tuples(self):
        self.assertEqual(LLMRater.take_n_uniques(None, 5), [])
        tuple_rows = [("val1", "val2"), ("val1", "val2"), ("val3", "val4")]
        uniques = LLMRater.take_n_uniques(tuple_rows, 5)
        self.assertEqual(len(uniques), 2)


if __name__ == "__main__":
    unittest.main()
