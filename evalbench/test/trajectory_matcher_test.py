import unittest
import sys
import os
from unittest.mock import patch, mock_open

# Add the parent directory to sys.path to find scorers
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorers.trajectorymatcher import TrajectoryMatcher


class TestTrajectoryMatcher(unittest.TestCase):

    def test_default_behavior_no_normalization(self):
        config = {}
        matcher = TrajectoryMatcher(config)

        expected = ["ToolA", "ToolB", "ToolSearch"]
        actual = ["ToolA", "ToolB", "ToolSearch"]

        score, explanation = matcher.compare(None, None, None, expected, None, None, None, actual, None, None)
        self.assertEqual(score, 100.0)
        self.assertIn("Jaccard Similarity Score: 100.00", explanation)

    def test_claude_behavior_remove_toolsearch(self):
        config = {"generator": "claude_code"}
        matcher = TrajectoryMatcher(config)

        expected = ["ToolA", "ToolSearch", "ToolB"]
        actual = ["ToolA", "ToolB"]

        # After normalization, expected should become ["ToolA", "ToolB"]
        # So it should match actual exactly.
        score, explanation = matcher.compare(None, None, None, expected, None, None, None, actual, None, None)
        self.assertEqual(score, 100.0)

    def test_claude_behavior_strip_mcp_prefix(self):
        config = {"generator": "claude_code"}
        matcher = TrajectoryMatcher(config)

        expected = ["mcp__server__toolA", "ToolB"]
        actual = ["toolA", "ToolB"]

        # After normalization, expected should become ["toolA", "ToolB"]
        score, explanation = matcher.compare(None, None, None, expected, None, None, None, actual, None, None)
        self.assertEqual(score, 100.0)

    def test_claude_behavior_combined(self):
        config = {"generator": "claude_code"}
        matcher = TrajectoryMatcher(config)

        expected = ["mcp__server__toolA", "ToolSearch", "ToolB"]
        actual = ["toolA", "ToolB"]

        score, explanation = matcher.compare(None, None, None, expected, None, None, None, actual, None, None)
        self.assertEqual(score, 100.0)

    def test_flexible_ordering(self):
        config = {"generator": "claude_code"}
        matcher = TrajectoryMatcher(config)

        expected = ["mcp__server__toolA", "ToolB"]
        actual = ["ToolB", "toolA"]

        # Jaccard similarity should ignore order
        score, explanation = matcher.compare(None, None, None, expected, None, None, None, actual, None, None)
        self.assertEqual(score, 100.0)

    def test_strict_ordering(self):
        config = {"generator": "claude_code", "enforce_order": True}
        matcher = TrajectoryMatcher(config)

        expected = ["mcp__server__toolA", "ToolB"]
        actual = ["ToolB", "toolA"]

        # Levenshtein distance will consider order.
        # expected normalized: ["toolA", "ToolB"]
        # actual normalized: ["ToolB", "toolA"]
        # They are different.
        score, explanation = matcher.compare(None, None, None, expected, None, None, None, actual, None, None)
        self.assertLess(score, 100.0)


class TestScoreTrajectoryMatcherIntegration(unittest.TestCase):

    @patch("builtins.open", new_callable=mock_open, read_data="generator: claude_code")
    @patch("yaml.safe_load")
    def test_score_detects_claude_from_config(self, mock_yaml_load, mock_file):
        mock_yaml_load.return_value = {"generator": "claude_code"}

        from scorers import score
        from dataset.evaloutput import EvalOutput

        class DummyEvalInput:
            def __init__(self, data):
                self.__dict__.update(data)

        dummy_input = DummyEvalInput({
            "id": 1,
            "generated_sql": "SELECT 1",
            "golden_sql": "SELECT 1",
            "query_type": "dql",
            "golden_result": [],
            "golden_error": "",
            "generated_result": [],
            "generated_error": "",
            "dialects": [],
            "database": "",
            "job_id": "",
            "eval_results": "",
            "golden_eval_results": ""
        })
        eval_output = EvalOutput(dummy_input)

        experiment_config = {
            "model_config": "dummy_path.yaml",
            "scorers": {
                "trajectory_matcher": {}
            }
        }

        scoring_results = []

        # We need to patch TrajectoryMatcher to check if it was called with generator="claude_code"
        with patch("scorers.trajectorymatcher.TrajectoryMatcher") as MockMatcher:
            score.compare(eval_output, experiment_config, scoring_results, None)
            # Check if TrajectoryMatcher was called with generator="claude_code"
            MockMatcher.assert_called_once()
            kwargs = MockMatcher.call_args.kwargs
            self.assertEqual(kwargs.get("generator"), "claude_code")


if __name__ == '__main__':
    unittest.main()
