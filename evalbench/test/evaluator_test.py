import collections
import datetime
from queue import Queue
import unittest
from unittest.mock import MagicMock, patch

from dataset.evalinput import EvalInputRequest
from dataset.evaloutput import EvalOutput
from evaluator.evaluator import Evaluator


class TestEvaluator(unittest.TestCase):

    @patch("evaluator.evaluator.record_successful_prompt_gen")
    @patch("evaluator.evaluator.record_successful_sql_gen")
    @patch("evaluator.evaluator.record_successful_sql_exec")
    @patch("evaluator.evaluator.record_successful_scoring")
    @patch("evaluator.evaluator.multi_trial_scorework.MultiTrialScorerWork")
    @patch("evaluator.evaluator.mprunner.MPRunner")
    @patch("evaluator.evaluator._process_futures_with_timeout")
    def test_evaluate_multi_trial(
        self,
        mock_process_futures,
        mock_mprunner_class,
        mock_multitrial_work_class,
        mock_record_scoring,
        mock_record_sql_exec,
        mock_record_sql_gen,
        mock_record_prompt,
    ):
        # Mock MPRunner to return new instances each time and store them
        created_runners = []

        def create_mock_runner(*args, **kwargs):
            runner = MagicMock()
            runner.futures = []

            def mock_execute_work(work):
                runner.futures.append(MagicMock())

            runner.execute_work.side_effect = mock_execute_work
            created_runners.append(runner)
            return runner

        mock_mprunner_class.side_effect = create_mock_runner

        # Mock _process_futures_with_timeout to yield based on input futures
        def side_effect(futures, future_to_eval_map, timeout):
            for f in futures:
                yield f, future_to_eval_map[f], False

        mock_process_futures.side_effect = side_effect

        # Mock MultiTrialScorerWork instance to be returned and passed to MPRunner
        mock_multitrial_work = MagicMock()
        mock_multitrial_work_class.return_value = mock_multitrial_work

        config = {"num_trials": 3, "runners": {}}
        evaluator = Evaluator(config)

        # Mock dataset
        eval_input = MagicMock(spec=EvalInputRequest)
        eval_input.__dict__ = {"id": "p1",
                               "nl_prompt": "test", "query_type": "dql"}
        dataset = [eval_input]

        db_queue = MagicMock()
        prompt_generator = MagicMock()
        model_generator = MagicMock()
        mock_progress = MagicMock()

        eval_outputs, scoring_results, multi_trial_scoring_results = (
            evaluator.evaluate(
                dataset=dataset,
                db_queue=db_queue,
                prompt_generator=prompt_generator,
                model_generator=model_generator,
                job_id="job1",
                run_time=datetime.datetime.now(),
                progress_reporting=mock_progress,
                global_models={},
                close_connections=False,
            )
        )

        # PromptGen: 1
        # SQLGen: 3
        # SQLExec: 3
        # Scoring: 3
        # MultiTrialScoring: 1
        # Total: 11 calls to execute_work.
        total_calls = sum(r.execute_work.call_count for r in created_runners)
        self.assertEqual(total_calls, 11)

        # Check that we have 3 eval_outputs (one for each trial)
        self.assertEqual(len(eval_outputs), 3)

        # Check that prompt_id was set correctly
        self.assertEqual(eval_outputs[0]["prompt_id"], "p1")

        # Check progress reporting calls
        mock_record_prompt.assert_called_once_with(mock_progress)
        self.assertEqual(mock_record_sql_gen.call_count, 3)
        mock_record_sql_gen.assert_called_with(mock_progress)
        self.assertEqual(mock_record_sql_exec.call_count, 3)
        mock_record_sql_exec.assert_called_with(mock_progress)
        self.assertEqual(mock_record_scoring.call_count, 3)
        mock_record_scoring.assert_called_with(mock_progress)

        # Check MultiTrialScorerWork instantiation
        mock_multitrial_work_class.assert_called_once_with(
            "p1",
            "test",
            eval_outputs,
            config,
            multi_trial_scoring_results,
            {},
            mock_progress,
        )

    @patch("evaluator.evaluator.record_successful_prompt_gen")
    @patch("evaluator.evaluator.record_successful_sql_gen")
    @patch("evaluator.evaluator.record_successful_sql_exec")
    @patch("evaluator.evaluator.record_successful_scoring")
    @patch("evaluator.evaluator.multi_trial_scorework.MultiTrialScorerWork")
    @patch("evaluator.evaluator.mprunner.MPRunner")
    @patch("evaluator.evaluator._process_futures_with_timeout")
    def test_evaluate_multi_trial_non_dql(
        self,
        mock_process_futures,
        mock_mprunner_class,
        mock_multitrial_work_class,
        mock_record_scoring,
        mock_record_sql_exec,
        mock_record_sql_gen,
        mock_record_prompt,
    ):
        created_runners = []

        def create_mock_runner(*args, **kwargs):
            runner = MagicMock()
            runner.futures = []

            def mock_execute_work(work):
                runner.futures.append(MagicMock())

            runner.execute_work.side_effect = mock_execute_work
            created_runners.append(runner)
            return runner

        mock_mprunner_class.side_effect = create_mock_runner

        def side_effect(futures, future_to_eval_map, timeout):
            for f in futures:
                yield f, future_to_eval_map[f], False

        mock_process_futures.side_effect = side_effect

        mock_multitrial_work = MagicMock()
        mock_multitrial_work_class.return_value = mock_multitrial_work

        config = {"num_trials": 3, "runners": {}}
        evaluator = Evaluator(config)

        # Mock non-DQL dataset (DML)
        eval_input = MagicMock(spec=EvalInputRequest)
        eval_input.__dict__ = {"id": "p1",
                               "nl_prompt": "test", "query_type": "dml"}
        dataset = [eval_input]

        db_queue = MagicMock()
        prompt_generator = MagicMock()
        model_generator = MagicMock()
        mock_progress = MagicMock()

        eval_outputs, scoring_results, multi_trial_scoring_results = (
            evaluator.evaluate(
                dataset=dataset,
                db_queue=db_queue,
                prompt_generator=prompt_generator,
                model_generator=model_generator,
                job_id="job1",
                run_time=datetime.datetime.now(),
                progress_reporting=mock_progress,
                global_models={},
                close_connections=False,
            )
        )

        # Expecting only 1 trial for DML!
        # PromptGen: 1
        # SQLGen: 1 (not 3)
        # SQLExec: 1 (not 3)
        # Scoring: 1 (not 3)
        # MultiTrialScoring: 1
        # Total: 5 calls to execute_work.
        total_calls = sum(r.execute_work.call_count for r in created_runners)
        self.assertEqual(total_calls, 5)

        # Check that we have only 1 eval_output (one trial)
        self.assertEqual(len(eval_outputs), 1)

        # Check progress reporting calls
        mock_record_prompt.assert_called_once_with(mock_progress)
        self.assertEqual(mock_record_sql_gen.call_count, 1)
        self.assertEqual(mock_record_sql_exec.call_count, 1)
        self.assertEqual(mock_record_scoring.call_count, 1)


if __name__ == "__main__":
    unittest.main()
