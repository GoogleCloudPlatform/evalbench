import unittest
from unittest.mock import MagicMock, patch
from work.multi_trial_scorework import MultiTrialScorerWork


class TestMultiTrialScorerWork(unittest.TestCase):

    @patch("work.multi_trial_scorework.multi_trial_score.compare")
    def test_run(self, mock_compare):
        mock_compare.return_value = [{"score": 100.0}]

        multi_trial_scoring_results = []
        work = MultiTrialScorerWork(
            prompt_id="p1",
            nl_prompt="test",
            trials=[],
            experiment_config={},
            multi_trial_scoring_results=multi_trial_scoring_results,
            global_models={},
        )

        results = work.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["score"], 100.0)
        self.assertEqual(len(multi_trial_scoring_results), 1)
        self.assertEqual(multi_trial_scoring_results[0]["score"], 100.0)
        mock_compare.assert_called_once()

    @patch("evaluator.progress_reporter.record_successful_scoring")
    @patch("work.multi_trial_scorework.multi_trial_score.compare")
    def test_run_with_progress_reporting(self, mock_compare, mock_record_scoring):
        mock_compare.return_value = [{"score": 100.0}]

        mock_progress = MagicMock()
        multi_trial_scoring_results = []
        work = MultiTrialScorerWork(
            prompt_id="p1",
            nl_prompt="test",
            trials=[],
            experiment_config={},
            multi_trial_scoring_results=multi_trial_scoring_results,
            global_models={},
            progress_reporting=mock_progress,
        )

        results = work.run()

        self.assertEqual(len(results), 1)
        mock_compare.assert_called_once()
        mock_record_scoring.assert_called_once_with(mock_progress)


if __name__ == "__main__":
    unittest.main()
