import unittest
from unittest.mock import MagicMock, patch
from scorers.llm_consistency_comparator import LLMConsistencyComparator


class TestLLMConsistencyComparator(unittest.TestCase):

    @patch("scorers.llm_consistency_comparator.get_generator")
    def test_compare_equivalent(self, mock_get_generator):
        mock_model = MagicMock()
        mock_model.generate.return_value = "Result: INFORMATION_MATCHES"
        mock_get_generator.return_value = mock_model

        config = {"model_config": "fake_config"}
        comparator = LLMConsistencyComparator(config, global_models={})

        trial1_result = [{"a": 1}]
        trial2_result = [
            {"a": 1, "b": 2}
        ]  # Different results to bypass exact match

        score, reason = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT a FROM t",
            trial1_execution_result=trial1_result,
            trial1_eval_result="",
            trial1_error="",
            trial2_query="SELECT a, b FROM t",
            trial2_execution_result=trial2_result,
            trial2_eval_result="",
            trial2_error="",
        )

        self.assertEqual(score, 100.0)
        self.assertIn("INFORMATION_MATCHES", reason)
        mock_model.generate.assert_called_once()

    @patch("scorers.llm_consistency_comparator.get_generator")
    def test_compare_inconsistent(self, mock_get_generator):
        mock_model = MagicMock()
        mock_model.generate.return_value = "Result: INCONSISTENT"
        mock_get_generator.return_value = mock_model

        config = {"model_config": "fake_config"}
        comparator = LLMConsistencyComparator(config, global_models={})

        trial1_result = [{"a": 1}]
        trial2_result = [{"a": 2}]

        score, reason = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=trial1_result,
            trial1_eval_result="",
            trial1_error="",
            trial2_query="SELECT 2",
            trial2_execution_result=trial2_result,
            trial2_eval_result="",
            trial2_error="",
        )

        self.assertEqual(score, 0.0)
        self.assertIn("INCONSISTENT", reason)
        mock_model.generate.assert_called_once()

    def test_compare_exact_match_shortcut(self):
        config = {"model_config": "fake_config"}
        # We don't patch get_generator here because it should not be called

        # Wait, __init__ calls get_generator. So we MUST patch it in __init__ or mock it.
        # Let's use patch as a context manager or just patch it for the whole test case.

        with patch(
            "scorers.llm_consistency_comparator.get_generator"
        ) as mock_get_generator:
            mock_model = MagicMock()
            mock_get_generator.return_value = mock_model

            comparator = LLMConsistencyComparator(config, global_models={})

            trial1_result = [{"a": 1}]
            trial2_result = [{"a": 1}]

            score, reason = comparator.compare(
                nl_prompt="test",
                trial1_query="SELECT 1",
                trial1_execution_result=trial1_result,
                trial1_eval_result="",
                trial1_error="",
                trial2_query="SELECT 1",
                trial2_execution_result=trial2_result,
                trial2_eval_result="",
                trial2_error="",
            )

            self.assertEqual(score, 100.0)
            self.assertIn("Exact Match was found", reason)
            mock_model.generate.assert_not_called()

    @patch("scorers.llm_consistency_comparator.get_generator")
    def test_compare_errors_equal(self, mock_get_generator):
        config = {"model_config": "fake_config"}
        mock_model = MagicMock()
        mock_get_generator.return_value = mock_model
        comparator = LLMConsistencyComparator(config, global_models={})

        score, reason = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=[],
            trial1_eval_result="",
            trial1_error="error",
            trial2_query="SELECT 1",
            trial2_execution_result=[],
            trial2_eval_result="",
            trial2_error="error",
        )

        self.assertEqual(score, 100.0)
        self.assertIn("Exact Match was found in errors", reason)
        mock_model.generate.assert_not_called()

    @patch("scorers.llm_consistency_comparator.get_generator")
    def test_compare_errors_equivalent(self, mock_get_generator):
        config = {"model_config": "fake_config"}
        mock_model = MagicMock()
        mock_model.generate.return_value = "Result: EQUIVALENT"
        mock_get_generator.return_value = mock_model
        comparator = LLMConsistencyComparator(config, global_models={})

        score, reason = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=[],
            trial1_eval_result="",
            trial1_error="error at line 5",
            trial2_query="SELECT 1",
            trial2_execution_result=[],
            trial2_eval_result="",
            trial2_error="error at line 10",
        )

        self.assertEqual(score, 100.0)
        self.assertIn("EQUIVALENT", reason)
        mock_model.generate.assert_called_once()

    @patch("scorers.llm_consistency_comparator.get_generator")
    def test_compare_errors_not_equivalent(self, mock_get_generator):
        config = {"model_config": "fake_config"}
        mock_model = MagicMock()
        mock_model.generate.return_value = "Result: NOT_EQUIVALENT"
        mock_get_generator.return_value = mock_model
        comparator = LLMConsistencyComparator(config, global_models={})

        score, reason = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=[],
            trial1_eval_result="",
            trial1_error="timeout",
            trial2_query="SELECT 1",
            trial2_execution_result=[],
            trial2_eval_result="",
            trial2_error="syntax error",
        )

        self.assertEqual(score, 0.0)
        self.assertIn("NOT_EQUIVALENT", reason)
        mock_model.generate.assert_called_once()

    @patch("scorers.llm_consistency_comparator.get_generator")
    def test_compare_one_error(self, mock_get_generator):
        config = {"model_config": "fake_config"}
        mock_model = MagicMock()
        mock_get_generator.return_value = mock_model
        comparator = LLMConsistencyComparator(config, global_models={})

        score, reason = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=[],
            trial1_eval_result="",
            trial1_error="error",
            trial2_query="SELECT 1",
            trial2_execution_result=[],
            trial2_eval_result="",
            trial2_error="",
        )

        self.assertEqual(score, 0.0)
        self.assertIn("Inconsistent: One trial failed", reason)
        mock_model.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
