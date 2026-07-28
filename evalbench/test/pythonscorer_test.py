import unittest
from unittest.mock import patch, MagicMock
from scorers.pythonscorer import PythonScorer
import json


class TestPythonScorer(unittest.TestCase):

    @patch('scorers.pythonscorer.subprocess.run')
    def test_python_scorer_pass(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"score": 100.0, "reason": "PASS"}'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        config = {"script_path": "dummy_script.py"}
        scorer = PythonScorer(config)

        score, reason = scorer.compare(
            nl_prompt="", golden_query="", query_type="",
            golden_execution_result="", golden_eval_result="", golden_error="",
            generated_query="", generated_execution_result="",
            generated_eval_result="", generated_error=""
        )

        self.assertEqual(score, 100.0)
        self.assertEqual(reason, "PASS")
        mock_run.assert_called_once()

    @patch('scorers.pythonscorer.subprocess.run')
    def test_python_scorer_fail(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Script error"
        mock_run.return_value = mock_result

        config = {"script_path": "dummy_script.py"}
        scorer = PythonScorer(config)

        score, reason = scorer.compare(
            nl_prompt="", golden_query="", query_type="",
            golden_execution_result="", golden_eval_result="", golden_error="",
            generated_query="", generated_execution_result="",
            generated_eval_result="", generated_error=""
        )

        self.assertEqual(score, 0.0)
        self.assertIn("FAIL: Script failed with exit code 1", reason)

    @patch('scorers.pythonscorer.subprocess.run')
    def test_python_scorer_invalid_json(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Not JSON"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        config = {"script_path": "dummy_script.py"}
        scorer = PythonScorer(config)

        score, reason = scorer.compare(
            nl_prompt="", golden_query="", query_type="",
            golden_execution_result="", golden_eval_result="", golden_error="",
            generated_query="", generated_execution_result="",
            generated_eval_result="", generated_error=""
        )

        self.assertEqual(score, 0.0)
        self.assertIn("FAIL: Failed to parse JSON", reason)

    @patch('scorers.pythonscorer.subprocess.run')
    def test_python_scorer_uv_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("uv not found")

        config = {"script_path": "dummy_script.py"}
        scorer = PythonScorer(config)

        score, reason = scorer.compare(
            nl_prompt="", golden_query="", query_type="",
            golden_execution_result="", golden_eval_result="", golden_error="",
            generated_query="", generated_execution_result="",
            generated_eval_result="", generated_error=""
        )

        self.assertEqual(score, 0.0)
        self.assertIn("FAIL: 'uv' command not found", reason)

    @patch('scorers.pythonscorer.subprocess.run')
    def test_multiple_python_scorers_in_compare(self, mock_run):
        from scorers import score as score_module

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"score": 100.0, "reason": "PASS"}'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        experiment_config = {
            "scorers": {
                "python_scorer_accuracy": {
                    "script_path": "path/to/acc_script.py"
                },
                "python_scorer_style": {
                    "script_path": "path/to/style_script.py",
                    "scorer_name": "custom_style_name"
                }
            }
        }
        eval_output_item = {
            "id": 1,
            "nl_prompt": "test",
            "golden_sql": "SELECT 1",
            "query_type": "SELECT",
            "golden_result": "",
            "golden_eval_results": "",
            "golden_error": "",
            "generated_sql": "SELECT 1",
            "generated_result": "",
            "eval_results": "",
            "generated_error": None,
            "dialects": "bigquery",
            "database": "db",
            "job_id": "job123",
        }
        scoring_results = []
        score_module.compare(eval_output_item, experiment_config, scoring_results, global_models=None)

        comparators = [res["comparator"] for res in scoring_results]
        self.assertIn("acc_script", comparators)
        self.assertIn("custom_style_name", comparators)
        self.assertEqual(len(scoring_results), 2)

    def test_multiple_python_scorers_aggregation(self):
        from reporting.analyzer import analyze_result

        scores = [
            {
                "id": "1",
                "comparator": "acc_script",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "2",
                "comparator": "acc_script",
                "score": 0,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "1",
                "comparator": "custom_style_name",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
            {
                "id": "2",
                "comparator": "custom_style_name",
                "score": 100,
                "generated_sql": "SELECT 1",
                "generated_error": None,
            },
        ]
        experiment_config = {
            "scorers": {
                "python_scorer_accuracy": {"script_path": "path/to/acc_script.py"},
                "python_scorer_style": {"script_path": "path/to/style_script.py", "scorer_name": "custom_style_name"},
            }
        }

        _, summary_df = analyze_result(scores, experiment_config)
        summary_dict = summary_df.set_index("metric_name").to_dict(orient="index")

        self.assertIn("python_scorer_accuracy", summary_dict)
        self.assertEqual(summary_dict["python_scorer_accuracy"]["correct_results_count"], 1)
        self.assertEqual(summary_dict["python_scorer_accuracy"]["total_results_count"], 2)

        self.assertIn("python_scorer_style", summary_dict)
        self.assertEqual(summary_dict["python_scorer_style"]["correct_results_count"], 2)
        self.assertEqual(summary_dict["python_scorer_style"]["total_results_count"], 2)


if __name__ == '__main__':
    unittest.main()
