import time
from evaluator.evaluator import Evaluator
from unittest.mock import MagicMock, patch
from concurrent.futures import Future
import unittest


class TestEvaluatorRobustness(unittest.TestCase):

    def setUp(self):
        self.config = {
            "runners": {
                "prompt_runners": 1,
                "sqlgen_runners": 1,
                "sqlexec_runners": 1,
                "scoring_runners": 1},
            "max_executions_per_minute": 60}

    @patch('concurrent.futures.wait')
    @patch('mp.mprunner.MPRunner')
    def test_evaluator_handles_as_completed_timeout(
            self, mock_mprunner_class, mock_wait):
        self.evaluator = Evaluator(self.config)
        self.evaluator.task_timeout_seconds = 0.01

        mock_runner = MagicMock()
        mock_mprunner_class.return_value = mock_runner

        mock_future = Future()
        mock_runner.futures = [mock_future]
        mock_runner.execute_work = lambda x: mock_runner.futures.append(
            mock_future)

        mock_wait.return_value = (set(), set([mock_future]))

        progress_reporting = {
            "prompt_i": MagicMock(),
            "gen_i": MagicMock(),
            "exec_i": MagicMock(),
            "score_i": MagicMock(),
            "total": 10,
            "lock": MagicMock()}

        with self.assertLogs('root', level='ERROR') as cm:
            self.evaluator.evaluate(
                dataset=[MagicMock()],
                db_queue=MagicMock(),
                prompt_generator=MagicMock(),
                model_generator=MagicMock(),
                job_id="test",
                run_time=MagicMock(),
                progress_reporting=progress_reporting,
                global_models={}
            )

        self.assertTrue(any(
            "Abandoning 1 hung futures after 0.01s timeout" in output for output in cm.output))

    @patch('concurrent.futures.wait')
    @patch('mp.mprunner.MPRunner')
    def test_evaluator_handles_future_exception(
            self, mock_mprunner_class, mock_wait):
        self.evaluator = Evaluator(self.config)

        mock_runner = MagicMock()
        mock_mprunner_class.return_value = mock_runner

        mock_future = Future()
        mock_future.set_exception(Exception("Worker crashed"))

        mock_runner.futures = []

        def append_future(work):
            mock_runner.futures.append(mock_future)

        mock_runner.execute_work = append_future

        def side_effect(uncompleted, *args, **kwargs):
            return (set(uncompleted), set())

        mock_wait.side_effect = side_effect

        progress_reporting = {
            "prompt_i": MagicMock(),
            "gen_i": MagicMock(),
            "exec_i": MagicMock(),
            "score_i": MagicMock(),
            "total": 10,
            "lock": MagicMock()}

        with self.assertLogs('root', level='ERROR') as cm:
            self.evaluator.evaluate(
                dataset=[MagicMock()],
                db_queue=MagicMock(),
                prompt_generator=MagicMock(),
                model_generator=MagicMock(),
                job_id="test",
                run_time=MagicMock(),
                progress_reporting=progress_reporting,
                global_models={}
            )

        self.assertTrue(
            any("Promptgen future error: Worker crashed" in output for output in cm.output))


if __name__ == '__main__':
    unittest.main()
