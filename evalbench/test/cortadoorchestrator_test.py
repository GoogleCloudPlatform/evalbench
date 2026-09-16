import json
import unittest
from unittest.mock import MagicMock, patch
from evaluator.cortadoorchestrator import CortadoOrchestrator


class TestCortadoOrchestrator(unittest.TestCase):

    @patch("evaluator.cortadoorchestrator.CortadoEvaluator")
    def test_evaluate_and_process_returns_5_tuple(self, mock_evaluator_class):
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = (
            [{"eval_id": "c1", "output": "sample_output"}],
            [{"eval_id": "c1", "score": 100}],
        )
        mock_evaluator_class.return_value = mock_evaluator

        config = {
            "runners": {"eval_runners": 1},
            "model_config": "fake_model_config",
        }
        orchestrator = CortadoOrchestrator(
            config=config,
            db_configs={},
            setup_config={},
        )

        orchestrator.evaluate([MagicMock()])

        # Call process and verify it returns a 5-tuple matching the Orchestrator contract
        result = orchestrator.process()
        self.assertEqual(len(result), 5)

        job_id, run_time, results_tf, scores_tf, multi_trial_scores_tf = result
        self.assertEqual(job_id, orchestrator.job_id)
        self.assertEqual(run_time, orchestrator.run_time)
        self.assertIsNotNone(results_tf)
        self.assertIsNotNone(scores_tf)
        self.assertIsNone(multi_trial_scores_tf)

        # Verify contents dumped to temp files
        with open(results_tf, "r") as f:
            results_data = json.load(f)
            self.assertEqual(len(results_data), 1)
            self.assertEqual(results_data[0]["eval_id"], "c1")

        with open(scores_tf, "r") as f:
            scores_data = json.load(f)
            self.assertEqual(len(scores_data), 1)
            self.assertEqual(scores_data[0]["score"], 100)


if __name__ == "__main__":
    unittest.main()
