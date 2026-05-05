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


if __name__ == '__main__':
    unittest.main()
