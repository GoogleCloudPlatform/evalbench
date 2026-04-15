import unittest
from unittest.mock import patch, MagicMock
from scorers.llmrater import LLMRater


class TestLLMRater(unittest.TestCase):
    def test_take_n_uniques_with_document_model(self):
        # A typical Document model returned result containing nested lists of dictionaries
        golden = [
            {"authors": [{"name": "Alice"}, {"name": "Bob"}]}
        ]
        try:
            result = LLMRater.take_n_uniques(golden, 50)
            self.assertEqual(len(result), 1)
        except TypeError as e:
            self.fail(f"take_n_uniques raised TypeError unexpectedly: {e}")

    def test_take_n_uniques_with_flat_dict(self):
        # Classic SQL row model where results are flat dicts
        golden = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 1, "name": "Alice"}  # Duplicate should be removed
        ]
        result = LLMRater.take_n_uniques(golden, 50)
        self.assertEqual(len(result), 2)

    def test_take_n_uniques_limit(self):
        # Ensure it respects the 'n' limit
        golden = [{"id": i} for i in range(100)]
        result = LLMRater.take_n_uniques(golden, 50)
        self.assertEqual(len(result), 50)

    def test_take_n_uniques_empty(self):
        # Edge case: empty list
        result = LLMRater.take_n_uniques([], 50)
        self.assertEqual(len(result), 0)

    @patch('scorers.llmrater.get_generator')
    def test_compare_empty_results_equivalent(self, mock_get_generator):
        mock_model = MagicMock()
        mock_model.generate.return_value = "Reasoning: Logic matches.\nResult: EQUIVALENT"
        mock_get_generator.return_value = mock_model

        config = {"model_config": "fake_config"}
        rater = LLMRater(config, global_models={})

        score, reason = rater.compare(
            nl_prompt="Show all users",
            golden_query="SELECT * FROM users",
            query_type="sql",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="SELECT * FROM users",
            generated_execution_result=[],
            generated_eval_result="",
            generated_error=""
        )

        self.assertEqual(score, 100)
        self.assertIn("EQUIVALENT", reason)
        mock_model.generate.assert_called_once()

    @patch('scorers.llmrater.get_generator')
    def test_compare_empty_results_not_equivalent(self, mock_get_generator):
        mock_model = MagicMock()
        mock_model.generate.return_value = "Reasoning: Missing condition.\nResult: NOT_EQUIVALENT"
        mock_get_generator.return_value = mock_model

        config = {"model_config": "fake_config"}
        rater = LLMRater(config, global_models={})

        score, reason = rater.compare(
            nl_prompt="Show all users",
            golden_query="SELECT * FROM users WHERE age > 10",
            query_type="sql",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="SELECT * FROM users",
            generated_execution_result=[],
            generated_eval_result="",
            generated_error=""
        )

        self.assertEqual(score, 0)
        self.assertIn("NOT_EQUIVALENT", reason)
        self.assertEqual(mock_model.generate.call_count, 2)

    @patch('scorers.llmrater.get_generator')
    def test_compare_exact_match_shortcut(self, mock_get_generator):
        # Should not call LLM if results match and are not empty
        mock_model = MagicMock()
        mock_get_generator.return_value = mock_model

        config = {"model_config": "fake_config"}
        rater = LLMRater(config, global_models={})

        golden_result = [{"id": 1}]
        generated_result = [{"id": 1}]

        score, reason = rater.compare(
            nl_prompt="Show all users",
            golden_query="SELECT * FROM users",
            query_type="sql",
            golden_execution_result=golden_result,
            golden_eval_result="",
            golden_error="",
            generated_query="SELECT * FROM users",
            generated_execution_result=generated_result,
            generated_eval_result="",
            generated_error=""
        )

        self.assertEqual(score, 100)
        self.assertIn("Exact Match was found", reason)
        mock_model.generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
