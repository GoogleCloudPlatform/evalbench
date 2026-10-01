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

    def _setup_mock_runner(self, mock_mprunner_class):
        def create_mock_runner(*args, **kwargs):
            runner = MagicMock()
            runner.futures = []

            def mock_execute_work(work):
                runner.futures.append(MagicMock())

            runner.execute_work.side_effect = mock_execute_work
            return runner

        mock_mprunner_class.side_effect = create_mock_runner

    @patch("evaluator.evaluator.mprunner.MPRunner")
    @patch("evaluator.evaluator._process_futures_with_timeout")
    def test_db_queue_timeout_custom_configured(
        self,
        mock_process_futures,
        mock_mprunner_class,
    ):
        self._setup_mock_runner(mock_mprunner_class)

        def side_effect(futures, future_to_eval_map, timeout):
            for f in futures:
                yield f, future_to_eval_map[f], False

        mock_process_futures.side_effect = side_effect

        config = {
            "runners": {
                "db_queue_timeout_seconds": 250,
                "task_timeout_seconds": 120,
            }
        }
        evaluator = Evaluator(config)
        self.assertEqual(evaluator.db_queue_timeout_seconds, 250)

        class DummyInput:
            def __init__(self, id="p1"):
                self.id = id
                self.nl_prompt = "test"
                self.query_type = "dql"
                self.database = "test_db"

        db_queue = MagicMock()
        evaluator.evaluate(
            dataset=[DummyInput("p1")],
            db_queue=db_queue,
            prompt_generator=MagicMock(),
            model_generator=MagicMock(),
            job_id="job1",
            run_time=datetime.datetime.now(),
            progress_reporting=MagicMock(),
            global_models={},
            close_connections=False,
        )

        db_queue.get.assert_called_with(timeout=250.0)

    @patch("evaluator.evaluator.mprunner.MPRunner")
    @patch("evaluator.evaluator._process_futures_with_timeout")
    def test_db_queue_timeout_unbounded_blocking(
        self,
        mock_process_futures,
        mock_mprunner_class,
    ):
        self._setup_mock_runner(mock_mprunner_class)

        def side_effect(futures, future_to_eval_map, timeout):
            for f in futures:
                yield f, future_to_eval_map[f], False

        mock_process_futures.side_effect = side_effect

        config = {"runners": {"db_queue_timeout_seconds": 0}}
        evaluator = Evaluator(config)

        class DummyInput:
            def __init__(self, id="p1"):
                self.id = id
                self.nl_prompt = "test"
                self.query_type = "dql"
                self.database = "test_db"

        db_queue = MagicMock()
        evaluator.evaluate(
            dataset=[DummyInput("p1")],
            db_queue=db_queue,
            prompt_generator=MagicMock(),
            model_generator=MagicMock(),
            job_id="job1",
            run_time=datetime.datetime.now(),
            progress_reporting=MagicMock(),
            global_models={},
            close_connections=False,
        )

        db_queue.get.assert_called_with(block=True)

    @patch("evaluator.evaluator.mprunner.MPRunner")
    @patch("evaluator.evaluator._process_futures_with_timeout")
    def test_db_queue_timeout_dynamic_scaling_default(
        self,
        mock_process_futures,
        mock_mprunner_class,
    ):
        self._setup_mock_runner(mock_mprunner_class)

        def side_effect(futures, future_to_eval_map, timeout):
            for f in futures:
                yield f, future_to_eval_map[f], False

        mock_process_futures.side_effect = side_effect

        # 600s task timeout -> 1.5 * 600 = 900s (between 300s floor and 1800s ceiling)
        config = {"runners": {"task_timeout_seconds": 600}}
        evaluator = Evaluator(config)

        class DummyInput:
            def __init__(self, id="p1"):
                self.id = id
                self.nl_prompt = "test"
                self.query_type = "dql"
                self.database = "test_db"

        dataset = [DummyInput(f"p{i}") for i in range(10)]

        db_queue = MagicMock()
        evaluator.evaluate(
            dataset=dataset,
            db_queue=db_queue,
            prompt_generator=MagicMock(),
            model_generator=MagicMock(),
            job_id="job1",
            run_time=datetime.datetime.now(),
            progress_reporting=MagicMock(),
            global_models={},
            close_connections=False,
        )

        db_queue.get.assert_called_with(timeout=900.0)

    @patch("evaluator.evaluator.mprunner.MPRunner")
    @patch("evaluator.evaluator._process_futures_with_timeout")
    def test_db_queue_timeout_floor_and_ceiling(
        self,
        mock_process_futures,
        mock_mprunner_class,
    ):
        self._setup_mock_runner(mock_mprunner_class)

        def side_effect(futures, future_to_eval_map, timeout):
            for f in futures:
                yield f, future_to_eval_map[f], False

        mock_process_futures.side_effect = side_effect

        class DummyInput:
            def __init__(self, id="p1"):
                self.id = id
                self.nl_prompt = "test"
                self.query_type = "dql"
                self.database = "test_db"

        # Case 1: Short task (100s) -> 1.5 * 100 = 150s, clamped to 300s floor
        evaluator_short = Evaluator({"runners": {"task_timeout_seconds": 100}})
        db_queue_1 = MagicMock()
        evaluator_short.evaluate(
            dataset=[DummyInput("p1")],
            db_queue=db_queue_1,
            prompt_generator=MagicMock(),
            model_generator=MagicMock(),
            job_id="job1",
            run_time=datetime.datetime.now(),
            progress_reporting=MagicMock(),
            global_models={},
            close_connections=False,
        )
        db_queue_1.get.assert_called_with(timeout=300.0)

        # Case 2: Very long task (2000s) -> 1.5 * 2000 = 3000s, clamped to 1800s ceiling
        evaluator_long = Evaluator({"runners": {"task_timeout_seconds": 2000}})
        db_queue_2 = MagicMock()
        evaluator_long.evaluate(
            dataset=[DummyInput("p1")],
            db_queue=db_queue_2,
            prompt_generator=MagicMock(),
            model_generator=MagicMock(),
            job_id="job2",
            run_time=datetime.datetime.now(),
            progress_reporting=MagicMock(),
            global_models={},
            close_connections=False,
        )
        db_queue_2.get.assert_called_with(timeout=1800.0)

    @patch("evaluator.evaluator.sqlexecwork.SQLExecWork")
    @patch("evaluator.evaluator.mprunner.MPRunner")
    @patch("evaluator.evaluator._process_futures_with_timeout")
    def test_head_of_line_blocking_queue_timeout_regression(
        self,
        mock_process_futures,
        mock_mprunner_class,
        mock_sqlexecwork_class,
    ):
        """Regression test for head-of-line blocking on db_queue.

        Prior to the fix, db_queue.get was hardcoded to timeout=180s. When a batch
        of items arrived, items queued past 180s timed out with queue.Empty.
        With the fix, dynamic queue_timeout defaults to 1.5x task_timeout (e.g. 900s),
        allowing healthy queued items to acquire a connection and succeed.
        """
        import queue

        self._setup_mock_runner(mock_mprunner_class)

        def side_effect(futures, future_to_eval_map, timeout):
            for f in futures:
                yield f, future_to_eval_map[f], False

        mock_process_futures.side_effect = side_effect

        # 600s task timeout -> 1.5 * 600 = 900s (> 180s)
        config = {"runners": {"task_timeout_seconds": 600}}
        evaluator = Evaluator(config)

        class DummyInput:
            def __init__(self, id="p1"):
                self.id = id
                self.nl_prompt = "test"
                self.query_type = "dql"
                self.database = "test_db"

        dataset = [DummyInput(f"p{i}") for i in range(10)]

        # Simulate a queue wait where connection acquisition takes >180s (e.g. 300s):
        # - Old code (timeout=180) fails with queue.Empty.
        # - Fixed code (timeout=900.0) succeeds.
        def fake_db_get(timeout=None, block=True):
            if timeout is not None and timeout <= 180:
                raise queue.Empty("Queue wait exceeded 180s limit")
            return MagicMock()

        db_queue = MagicMock()
        db_queue.get.side_effect = fake_db_get

        eval_outputs, _, _ = evaluator.evaluate(
            dataset=dataset,
            db_queue=db_queue,
            prompt_generator=MagicMock(),
            model_generator=MagicMock(),
            job_id="job1",
            run_time=datetime.datetime.now(),
            progress_reporting=MagicMock(),
            global_models={},
            close_connections=False,
        )

        # Verify that all items successfully acquired DB connection without timing out
        for eval_output in eval_outputs:
            self.assertIsNone(eval_output.get("generated_error"))


if __name__ == "__main__":
    unittest.main()
