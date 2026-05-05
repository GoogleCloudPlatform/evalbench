import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from evaluator.oneshotorchestrator import OneShotOrchestrator


class TestOneShotOrchestrator(unittest.TestCase):

    @patch("evaluator.oneshotorchestrator.Evaluator")
    @patch("evaluator.oneshotorchestrator.get_context")
    def test_evaluate_and_process(self, mock_get_context, mock_evaluator_class):
        # Mock Manager
        mock_manager = MagicMock()
        mock_get_context.return_value.Manager.return_value = mock_manager

        mock_evaluator = MagicMock()
        # Return 3 values as expected by updated Evaluator
        mock_evaluator.evaluate.return_value = (
            [{"id": "t1"}],
            [{"score": 100}],
            [{"prompt_id": "p1", "score": 100}],
        )
        mock_evaluator_class.return_value = mock_evaluator

        config = {
            "runners": {"eval_runners": 1},
            "model_config": "fake_model_config",
        }
        orchestrator = OneShotOrchestrator(
            config,
            db_configs={
                "sql": [{
                    "database": "fake",
                    "db_type": "spanner",
                    "database_path": "fake_path",
                }]
            },
            setup_config={},
        )

        with patch(
            "evaluator.oneshotorchestrator.breakdown_datasets"
        ) as mock_breakdown:
            mock_breakdown.return_value = (
                {"sql": {
                    "fake": {"dql": [{"id": "p1", "nl_prompt": "test"}]}}},
                1,
                1,
            )

            # Mock databases.get_database to avoid real connections
            with patch(
                "evaluator.oneshotorchestrator.databases.get_database"
            ) as mock_get_db:
                mock_get_db.return_value = MagicMock()

                with patch(
                    "evaluator.oneshotorchestrator.build_db_queue"
                ) as mock_build_queue:
                    mock_build_queue.return_value = MagicMock()

                    with patch(
                        "evaluator.oneshotorchestrator.prompts.get_generator"
                    ) as mock_get_prompt_gen:
                        mock_get_prompt_gen.return_value = MagicMock()

                        with patch(
                            "evaluator.oneshotorchestrator.models.get_generator"
                        ) as mock_get_model_gen:
                            mock_get_model_gen.return_value = MagicMock()

                            orchestrator.evaluate(
                                [{"dialect": "sql", "database": "fake"}])

        # Now call process and verify output
        job_id, run_time, results_tf, scores_tf, multi_trial_scores_tf = (
            orchestrator.process()
        )

        self.assertTrue(results_tf.endswith(".json"))
        self.assertTrue(scores_tf.endswith(".json"))
        self.assertTrue(multi_trial_scores_tf.endswith(".json"))

        # Read multi_trial_scores_tf and verify content
        with open(multi_trial_scores_tf, "r") as f:
            data = json.load(f)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["prompt_id"], "p1")

    @patch("evaluator.oneshotorchestrator.setup_progress_reporting")
    @patch("evaluator.oneshotorchestrator.Evaluator")
    @patch("evaluator.oneshotorchestrator.get_context")
    def test_evaluate_with_progress_reporting(
        self, mock_get_context, mock_evaluator_class, mock_setup_progress
    ):
        mock_manager = MagicMock()
        mock_get_context.return_value.Manager.return_value.__enter__.return_value = (
            mock_manager
        )
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = ([], [], [])
        mock_evaluator_class.return_value = mock_evaluator
        mock_setup_progress.return_value = (None, {}, None, None, None)

        config = {
            "runners": {"eval_runners": 1},
            "model_config": "fake_model_config",
            "num_trials": 3,
        }
        orchestrator = OneShotOrchestrator(
            config,
            db_configs={
                "sql": [{
                    "database": "fake",
                    "db_type": "spanner",
                    "database_path": "fake_path",
                }]
            },
            setup_config={},
            report_progress=True,
        )

        with patch(
            "evaluator.oneshotorchestrator.breakdown_datasets"
        ) as mock_breakdown:
            mock_breakdown.return_value = (
                {"sql": {
                    "fake": {"dql": [{"id": "p1", "nl_prompt": "test"}]}}},
                1,
                1,
            )
            with patch(
                "evaluator.oneshotorchestrator.databases.get_database"
            ) as mock_get_db:
                mock_get_db.return_value = MagicMock()
                with patch(
                    "evaluator.oneshotorchestrator.build_db_queue"
                ) as mock_build_queue:
                    mock_build_queue.return_value = MagicMock()
                    with patch(
                        "evaluator.oneshotorchestrator.prompts.get_generator"
                    ) as mock_get_prompt_gen:
                        mock_get_prompt_gen.return_value = MagicMock()
                        with patch(
                            "evaluator.oneshotorchestrator.models.get_generator"
                        ) as mock_get_model_gen:
                            mock_get_model_gen.return_value = MagicMock()

                            orchestrator.evaluate(
                                [{"dialect": "sql", "database": "fake"}])

        mock_setup_progress.assert_called_once_with(mock_manager, 1, 1, 3)


if __name__ == "__main__":
    unittest.main()
