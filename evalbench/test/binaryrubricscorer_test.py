import unittest
from unittest.mock import patch, MagicMock
from scorers.binaryrubricscorer import BinaryRubricScorer


class TestBinaryRubricScorer(unittest.TestCase):

    @patch('scorers.binaryrubricscorer.get_generator')
    def test_compare_rubric_pass(self, mock_get_generator):
        mock_model = MagicMock()
        mock_model.generate.return_value = "PASS\nCriterion 1 satisfied."
        mock_get_generator.return_value = mock_model

        config = {"model_config": "fake_config"}
        scorer = BinaryRubricScorer(
            config, global_models={}, criterion="Criterion 1", index=0
        )

        score, reason = scorer.compare(
            nl_prompt="",
            golden_query="",
            query_type="",
            golden_execution_result="",
            golden_eval_result="",
            golden_error="",
            generated_query="",
            generated_execution_result="",
            generated_eval_result=(
                '{"conversation_history": "[]", '
                '"scenario": {"binary_rubric": ["Criterion 1"]}}'
            ),
            generated_error=""
        )

        self.assertEqual(score, 100.0)
        self.assertIn("PASS", reason)
        self.assertEqual(scorer.name, "binary_rubric_scorer_0")
        mock_model.generate.assert_called_once()

    @patch('scorers.binaryrubricscorer.get_generator')
    def test_compare_rubric_partial_fail(self, mock_get_generator):
        mock_model = MagicMock()
        mock_model.generate.return_value = (
            "FAIL\nCriterion 1 was not satisfied."
        )
        mock_get_generator.return_value = mock_model

        config = {"model_config": "fake_config"}
        scorer = BinaryRubricScorer(
            config, global_models={}, criterion="Criterion 1", index=0
        )

        score, reason = scorer.compare(
            nl_prompt="",
            golden_query="",
            query_type="",
            golden_execution_result="",
            golden_eval_result="",
            golden_error="",
            generated_query="",
            generated_execution_result="",
            generated_eval_result=(
                '{"conversation_history": "[]", '
                '"scenario": {"binary_rubric": ["Criterion 1"]}}'
            ),
            generated_error=""
        )

        self.assertEqual(score, 0.0)
        self.assertIn("FAIL", reason)
        self.assertEqual(scorer.name, "binary_rubric_scorer_0")
        mock_model.generate.assert_called_once()

    @patch('scorers.binaryrubricscorer.get_generator')
    def test_compare_missing_rubric_defaults_pass(self, mock_get_generator):
        mock_model = MagicMock()
        mock_get_generator.return_value = mock_model

        config = {"model_config": "fake_config"}
        scorer = BinaryRubricScorer(config, global_models={})

        score, reason = scorer.compare(
            nl_prompt="",
            golden_query="",
            query_type="",
            golden_execution_result="",
            golden_eval_result="",
            golden_error="",
            generated_query="",
            generated_execution_result="",
            generated_eval_result=(
                '{"conversation_history": "[]", "scenario": {}}'
            ),
            generated_error=""
        )

        self.assertEqual(score, 100.0)
        self.assertIn("No rubric defined", reason)
        mock_model.generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
