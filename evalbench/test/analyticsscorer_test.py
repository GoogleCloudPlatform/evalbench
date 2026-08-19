"""Unit tests for AnalyticsScorer (Brewmax Data Result Rater in Evalbench)."""

import unittest
from unittest.mock import MagicMock, patch

from scorers.analyticsscorer import AnalyticsScorer


class TestAnalyticsScorer(unittest.TestCase):

    def test_init_missing_model_config(self):
        with self.assertRaises(ValueError):
            AnalyticsScorer({}, global_models={})

    @patch("scorers.analyticsscorer.get_generator")
    def test_init_success(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})
        self.assertEqual(scorer.name, "analytics_scorer")
        self.assertEqual(scorer.max_data_chars, 8000)
        self.assertEqual(scorer.query_label, "SQL Query")

    @patch("scorers.analyticsscorer.get_generator")
    def test_render_data_truncation(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer(
            {"model_config": "model.yaml", "max_data_chars": 50},
            global_models={},
        )
        long_data = [{"id": i, "description": "some long text field value"} for i in range(10)]
        rendered = scorer._render_data(long_data)
        self.assertIn("... [truncated ", rendered)
        self.assertLess(len(rendered), 150)

    @patch("scorers.analyticsscorer.get_generator")
    def test_parse_verdict_pass(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        response = (
            "Reasoning: The trial result matches the ground truth correctly.\n\n"
            "VERDICT: PASS"
        )
        score, log = scorer._parse_verdict(response)
        self.assertEqual(score, 100.0)
        self.assertEqual(log, response)

    @patch("scorers.analyticsscorer.get_generator")
    def test_parse_verdict_fail(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        response = (
            "Reasoning: The trial query used the wrong aggregation.\n\n"
            "VERDICT: FAIL"
        )
        score, log = scorer._parse_verdict(response)
        self.assertEqual(score, 0.0)
        self.assertEqual(log, response)

    @patch("scorers.analyticsscorer.get_generator")
    def test_compare_golden_error(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        score, log = scorer.compare(
            nl_prompt="List users",
            golden_query="SELECT * FROM users",
            query_type="DQL",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="Table not found",
            generated_query="SELECT * FROM users",
            generated_execution_result=[{"id": 1}],
            generated_eval_result="",
            generated_error="",
        )
        self.assertEqual(score, 0.0)
        self.assertIn("Golden query failed to execute", log)

    @patch("scorers.analyticsscorer.get_generator")
    def test_compare_generated_error_with_golden_data(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        score, log = scorer.compare(
            nl_prompt="List users",
            golden_query="SELECT * FROM users",
            query_type="DQL",
            golden_execution_result=[{"id": 1}],
            golden_eval_result="",
            golden_error="",
            generated_query="SELECT * FROM non_existent",
            generated_execution_result=[],
            generated_eval_result="",
            generated_error="Table not found",
        )
        self.assertEqual(score, 0.0)
        self.assertIn("Generated query failed to execute", log)

    @patch("scorers.analyticsscorer.get_generator")
    def test_compare_full_prompt_generation(self, mock_get_gen):
        mock_model = MagicMock()
        mock_model.generate.return_value = (
            "Reasoning: The column alias is different but the data is the same.\n"
            "VERDICT: PASS"
        )
        mock_get_gen.return_value = mock_model

        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        score, log = scorer.compare(
            nl_prompt="How many active users are there?",
            golden_query="SELECT COUNT(*) AS active_cnt FROM users WHERE active = true",
            query_type="DQL",
            golden_execution_result=[{"active_cnt": 42}],
            golden_eval_result="",
            golden_error="",
            generated_query="SELECT COUNT(id) AS total_active FROM users WHERE active = true",
            generated_execution_result=[{"total_active": 42}],
            generated_eval_result="",
            generated_error="",
        )
        self.assertEqual(score, 100.0)
        self.assertIn("VERDICT: PASS", log)

        # Check prompt content sent to generate
        called_prompt = mock_model.generate.call_args[0][0]
        self.assertIn("How many active users are there?", called_prompt)
        self.assertIn("SELECT COUNT(*) AS active_cnt", called_prompt)
        self.assertIn("SELECT COUNT(id) AS total_active", called_prompt)
        self.assertIn("VERDICT: PASS` or `VERDICT: FAIL", called_prompt)


if __name__ == "__main__":
    unittest.main()
