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

    def test_binary_rubric_scorer_aggregation(self):
        from reporting.analyzer import analyze_result

        scores = [
            {
                "id": "1",
                "comparator": "binary_rubric_scorer_0",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "1",
                "comparator": "binary_rubric_scorer_1",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "2",
                "comparator": "binary_rubric_scorer_0",
                "score": 0,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "2",
                "comparator": "binary_rubric_scorer_1",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
        ]
        experiment_config = {
            "scorers": {
                "binary_rubric_scorer": {
                    "model_config": "fake_config"
                }
            }
        }

        _, summary_df = analyze_result(scores, experiment_config)
        summary_dict = summary_df.set_index(
            "metric_name"
        ).to_dict(orient="index")

        self.assertIn("binary_rubric_scorer", summary_dict)
        # Total scores = 4 rows, 3 scored 100 -> 3/4 = 75%
        self.assertEqual(
            summary_dict["binary_rubric_scorer"]["correct_results_count"], 3
        )
        self.assertEqual(
            summary_dict["binary_rubric_scorer"]["total_results_count"], 4
        )

    def test_custom_named_binary_rubric_scorer_aggregation(self):
        from reporting.analyzer import analyze_result

        scores = [
            {
                "id": "1",
                "comparator": "my_rubric_0",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "1",
                "comparator": "my_rubric_1",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "2",
                "comparator": "my_rubric_0",
                "score": 0,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "2",
                "comparator": "my_rubric_1",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
        ]
        experiment_config = {
            "scorers": {
                "my_rubric": {
                    "type": "binary_rubric_scorer",
                    "model_config": "fake_config"
                }
            }
        }

        _, summary_df = analyze_result(scores, experiment_config)
        summary_dict = summary_df.set_index(
            "metric_name"
        ).to_dict(orient="index")

        self.assertIn("my_rubric", summary_dict)
        self.assertEqual(
            summary_dict["my_rubric"]["correct_results_count"], 3
        )
        self.assertEqual(
            summary_dict["my_rubric"]["total_results_count"], 4
        )


if __name__ == '__main__':
    unittest.main()
