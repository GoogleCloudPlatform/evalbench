"""Unit tests for AnalyticsScorer (Data Results Rater in Evalbench)."""

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
        self.assertEqual(scorer.max_data_result_entries, 50)
        self.assertTrue(scorer.skip_llm_on_exact_match)
        self.assertEqual(scorer.query_label, "SQL Query")

    @patch("scorers.analyticsscorer.get_generator")
    def test_render_data_truncation_uses_cell_budget(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer(
            {"model_config": "model.yaml", "max_data_result_entries": 6},
            global_models={},
        )
        # 10 rows x 2 columns = 20 cells > 6, so keep 6 // 2 = 3 rows.
        long_data = [{"id": i, "name": f"User_{i}"} for i in range(10)]
        rendered = scorer._format_data_result(long_data, "trial")
        self.assertTrue(
            rendered.startswith(
                "(trial dataframe was truncated from 10 rows to 3 rows for display.) "
            )
        )
        self.assertIn("User_2", rendered)
        self.assertNotIn("User_3", rendered)

    @patch("scorers.analyticsscorer.get_generator")
    def test_render_data_wide_result_keeps_at_least_one_row(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})
        wide_row = {f"c{i}": i for i in range(60)}
        rendered = scorer._format_data_result([wide_row, wide_row], "golden")
        self.assertTrue(
            rendered.startswith(
                "(golden dataframe was truncated from 2 rows to 1 rows for display.) "
            )
        )
        self.assertIn("c59", rendered)

    @patch("scorers.analyticsscorer.get_generator")
    def test_render_data_keeps_duplicate_rows(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})
        rendered = scorer._format_data_result([{"a": 1}] * 3, "golden")
        self.assertNotIn("truncated", rendered)
        self.assertEqual([line.strip() for line in rendered.split("\n")[1:]], ["1", "1", "1"])

    @patch("scorers.analyticsscorer.get_generator")
    def test_render_trajectory_omits_data_block_when_empty(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})
        trajectory = scorer._render_trajectory("SELECT 1 WHERE FALSE", [], "trial")
        self.assertIn('SQL Query:\n    "SELECT 1 WHERE FALSE"', trajectory)
        self.assertNotIn("Data:", trajectory)

        trajectory = scorer._render_trajectory("SELECT 1 AS x", [{"x": 1}], "trial")
        self.assertIn(' Data:\n    " x\n 1"', trajectory)

    @patch("scorers.analyticsscorer.get_generator")
    def test_init_max_rows_is_ignored(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        with self.assertLogs(level="WARNING"):
            scorer = AnalyticsScorer(
                {"model_config": "model.yaml", "max_rows": 3}, global_models={}
            )
        self.assertEqual(scorer.max_data_result_entries, 50)

    def test_parse_verdict_tolerates_markdown(self):
        for response in (
            "Reasoning.\n**VERDICT:** PASS",
            "Reasoning.\nVERDICT: **PASS**",
            "Reasoning.\n`VERDICT: PASS`",
            "Reasoning.\nPASS",
        ):
            with self.subTest(response=response):
                score, _ = AnalyticsScorer._parse_verdict(response)
                self.assertEqual(score, 100.0)

    def test_parse_verdict_prefers_final_line(self):
        response = (
            "Output `VERDICT: PASS` or `VERDICT: FAIL`.\n"
            "Check 3: NO.\n"
            "VERDICT: FAIL"
        )
        score, _ = AnalyticsScorer._parse_verdict(response)
        self.assertEqual(score, 0.0)

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
    def test_compare_golden_error_raises(self, mock_get_gen):
        mock_get_gen.return_value = MagicMock()
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        with self.assertRaisesRegex(ValueError, "Golden query failed to execute"):
            scorer.compare(
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
        self.assertIn("VERDICT: PASS\nVERDICT: FAIL", called_prompt)
        self.assertIn("Check 4 - No Invalid Columns", called_prompt)
        self.assertIn('     Data:\n    " active_cnt\n         42"', called_prompt)

    @patch("scorers.analyticsscorer.get_generator")
    def test_compare_empty_trial_trajectory_fails_without_llm(self, mock_get_gen):
        mock_model = MagicMock()
        mock_get_gen.return_value = mock_model
        scorer = AnalyticsScorer({"model_config": "model.yaml"}, global_models={})

        score, log = scorer.compare(
            nl_prompt="List users",
            golden_query="SELECT id FROM users",
            query_type="DQL",
            golden_execution_result=[{"id": 1}],
            golden_eval_result="",
            golden_error="",
            generated_query="",
            generated_execution_result=[],
            generated_eval_result="",
            generated_error="",
        )
        self.assertEqual(score, 0.0)
        self.assertIn("trial trajectory is empty", log)
        mock_model.generate.assert_not_called()

    @patch("scorers.analyticsscorer.with_cache_execute", return_value=None)
    @patch("scorers.analyticsscorer.get_cache_client")
    @patch("scorers.analyticsscorer.get_generator")
    def test_compare_cached_model_failure_raises(
        self, mock_get_gen, mock_cache, unused_mock_with_cache
    ):
        mock_get_gen.return_value = MagicMock()
        mock_cache.return_value = MagicMock()
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


if __name__ == "__main__":
    unittest.main()
