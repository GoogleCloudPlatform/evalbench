"""Unit tests for AnalyticsScorer (Conversational Analytics Data Results Rater in Evalbench)."""

import json
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
        self.assertEqual(scorer.max_rows, 50)
        self.assertEqual(scorer.query_label, "SQL Query")

    @patch("scorers.analyticsscorer.get_generator")
    def test_render_data_truncation_yields_valid_json(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer(
            {"model_config": "model.yaml", "max_rows": 3},
            global_models={},
        )
        long_data = [{"id": i, "name": f"User_{i}"} for i in range(10)]
        rendered = scorer._render_data(long_data)
        self.assertIn("[Note: Displaying 3 of 10 total rows]", rendered)

        # Ensure the serialized JSON payload prefix is 100% valid parseable JSON
        json_part = rendered.split("\n")[0]
        parsed = json.loads(json_part)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0]["name"], "User_0")

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
    def test_parse_verdict_unparseable_defaults_to_zero_with_log(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        # Sentences with "passes" or "surpasses" without VERDICT: label should NOT score 100
        response = "The trial response surpasses expectations and passes all tests."
        score, log = scorer._parse_verdict(response)
        self.assertEqual(score, 0.0)
        self.assertIn("Could not parse valid VERDICT", log)

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
    def test_compare_generated_error_with_empty_golden_data(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        score, log = scorer.compare(
            nl_prompt="List users older than 200",
            golden_query="SELECT * FROM users WHERE age > 200",
            query_type="DQL",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="SELECT * FROM users_typo",
            generated_execution_result=[],
            generated_eval_result="",
            generated_error="Syntax error: no such table users_typo",
        )
        self.assertEqual(score, 0.0)
        self.assertIn("Generated query failed to execute", log)
        # Verify LLM judge was NOT called when generated query errored
        mock_get_gen.return_value.generate.assert_not_called()

    @patch("scorers.analyticsscorer.get_generator")
    def test_compare_exact_match_short_circuit(self, mock_get_gen):
        mock_model = MagicMock()
        mock_get_gen.return_value = mock_model

        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        score, log = scorer.compare(
            nl_prompt="List active users",
            golden_query="SELECT id, name FROM users WHERE active = true",
            query_type="DQL",
            golden_execution_result=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            golden_eval_result="",
            golden_error="",
            generated_query="SELECT name, id FROM users WHERE active = true",
            generated_execution_result=[{"id": 2, "name": "Bob"}, {"id": 1, "name": "Alice"}],
            generated_eval_result="",
            generated_error="",
        )
        self.assertEqual(score, 100.0)
        self.assertIn("Skipped. Exact Match was found.", log)
        mock_model.generate.assert_not_called()

    @patch("scorers.analyticsscorer.get_generator")
    def test_compare_model_exception_propagates(self, mock_get_gen):
        mock_model = MagicMock()
        mock_model.generate.side_effect = RuntimeError("Quota exceeded")
        mock_get_gen.return_value = mock_model

        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        with self.assertRaises(RuntimeError):
            scorer.compare(
                nl_prompt="Query",
                golden_query="SELECT 1",
                query_type="DQL",
                golden_execution_result=[{"1": 1}],
                golden_eval_result="",
                golden_error="",
                generated_query="SELECT 2",
                generated_execution_result=[{"2": 2}],
                generated_eval_result="",
                generated_error="",
            )

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
            generated_execution_result=[{"total_active": 42, "extra_meta": "ok"}],
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
