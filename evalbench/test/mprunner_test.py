import concurrent.futures
import threading
import time
import unittest

from mp import mprunner
from work.work import Work


class _RecordingWork(Work):
    """Work item that records the thread it ran on."""

    def __init__(self, threads: list, barrier: threading.Event | None = None):
        self.threads = threads
        self.barrier = barrier
        self.started = threading.Event()

    def run(self, work_config=None):
        self.started.set()
        if self.barrier is not None:
            self.barrier.wait()
        self.threads.append(threading.current_thread())
        return "done"


def _live(threads) -> int:
    return sum(1 for t in threads if t.is_alive())


class TestMPRunnerShutdown(unittest.TestCase):

    def test_workers_stay_alive_until_shutdown(self):
        """Idle workers persist after their work finishes, and exit on shutdown."""
        threads = []
        runner = mprunner.MPRunner(3)
        for _ in range(3):
            runner.execute_work(_RecordingWork(threads))
        concurrent.futures.wait(runner.futures, timeout=30)

        self.assertEqual(len(threads), 3)
        worker_threads = set(threads)
        # All work is complete, but the pool keeps its workers parked.
        self.assertEqual(_live(worker_threads), len(worker_threads))

        runner.shutdown()
        for t in worker_threads:
            t.join(timeout=30)
        self.assertEqual(_live(worker_threads), 0)

    def test_context_manager_drains_before_shutting_down(self):
        """`with` runs queued work to completion, like Executor.__exit__."""
        threads = []
        with mprunner.MPRunner(1) as runner:
            # The second item cannot start until the first returns, so it is
            # still queued when the block exits.
            runner.execute_work(_RecordingWork(threads))
            runner.execute_work(_RecordingWork(threads))

        self.assertEqual(len(threads), 2)
        self.assertTrue(all(f.done() and not f.cancelled()
                            for f in runner.futures))
        for t in set(threads):
            t.join(timeout=30)
        self.assertEqual(_live(set(threads)), 0)

    def test_context_manager_shuts_down_on_exception(self):
        threads = []
        runner = mprunner.MPRunner(2)
        with self.assertRaises(ValueError):
            with runner:
                runner.execute_work(_RecordingWork(threads))
                concurrent.futures.wait(runner.futures, timeout=30)
                raise ValueError("boom")

        for t in set(threads):
            t.join(timeout=30)
        self.assertEqual(_live(set(threads)), 0)

    def test_shutdown_does_not_block_on_hung_work(self):
        """A hung work item must not stall shutdown of the rest of the pool."""
        release = threading.Event()
        threads = []
        runner = mprunner.MPRunner(2)
        hung = _RecordingWork(threads, barrier=release)
        runner.execute_work(hung)
        self.assertTrue(hung.started.wait(timeout=30))

        try:
            start = time.monotonic()
            runner.shutdown()
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 5.0)
            self.assertFalse(runner.futures[0].done())
        finally:
            release.set()
        concurrent.futures.wait(runner.futures, timeout=30)

    def test_shutdown_cancels_queued_work(self):
        """Work still queued behind a busy worker is cancelled, not run."""
        release = threading.Event()
        threads = []
        runner = mprunner.MPRunner(1)
        blocker = _RecordingWork(threads, barrier=release)
        runner.execute_work(blocker)
        self.assertTrue(blocker.started.wait(timeout=30))
        # Second item cannot start: the single worker is blocked on the first.
        runner.execute_work(_RecordingWork(threads))

        try:
            runner.shutdown()
            self.assertTrue(runner.futures[1].cancelled())
        finally:
            release.set()
        # Only the running item can finish. A cancelled future never reaches
        # CANCELLED_AND_NOTIFIED, so waiting on it would burn the full timeout.
        concurrent.futures.wait([runner.futures[0]], timeout=30)
        self.assertEqual(len(threads), 1)

    def test_shutdown_keeps_queued_work_when_cancel_disabled(self):
        """`cancel_futures=False` lets queued work run so it can free resources."""
        release = threading.Event()
        threads = []
        runner = mprunner.MPRunner(1)
        blocker = _RecordingWork(threads, barrier=release)
        runner.execute_work(blocker)
        self.assertTrue(blocker.started.wait(timeout=30))
        runner.execute_work(_RecordingWork(threads))

        runner.shutdown(cancel_futures=False)
        self.assertFalse(runner.futures[1].cancelled())
        release.set()
        concurrent.futures.wait(runner.futures, timeout=30)
        self.assertEqual(len(threads), 2)


class TestEvaluatorReleasesRunners(unittest.TestCase):
    """The stage pools are built per sub-dataset, so they must be released."""

    def _runner_names(self):
        return ["promptrunner", "genrunner", "sqlrunner", "scoringrunner"]

    def test_evaluate_shuts_down_every_stage_runner(self):
        from unittest.mock import MagicMock, patch

        from evaluator.evaluator import Evaluator

        created = []

        def make_runner(*args, **kwargs):
            runner = MagicMock()
            runner.futures = []
            created.append(runner)
            return runner

        with patch("evaluator.evaluator.mprunner.MPRunner", side_effect=make_runner):
            evaluator = Evaluator({"runners": {}})
            prompt_generator = MagicMock()
            evaluator.evaluate(
                dataset=[],
                db_queue=None,
                prompt_generator=prompt_generator,
                model_generator=MagicMock(),
                job_id="job",
                run_time=None,
                progress_reporting=None,
                global_models={},
            )

        self.assertEqual(len(created), 4)
        for runner in created:
            runner.shutdown.assert_called_once()
        # Pools are built in stage order, so created[2] is the sqlexec pool.
        # Its queued work holds DB connections that only SQLExecWork.run
        # returns, so it must not be cancelled.
        created[2].shutdown.assert_called_once_with(cancel_futures=False)

    def test_evaluate_shuts_down_runners_when_pipeline_raises(self):
        from unittest.mock import MagicMock, patch

        from evaluator.evaluator import Evaluator

        created = []

        def make_runner(*args, **kwargs):
            runner = MagicMock()
            runner.futures = []
            created.append(runner)
            return runner

        prompt_generator = MagicMock()
        prompt_generator.setup.side_effect = RuntimeError("setup failed")

        with patch("evaluator.evaluator.mprunner.MPRunner", side_effect=make_runner):
            evaluator = Evaluator({"runners": {}})
            with self.assertRaises(RuntimeError):
                evaluator.evaluate(
                    dataset=[],
                    db_queue=None,
                    prompt_generator=prompt_generator,
                    model_generator=MagicMock(),
                    job_id="job",
                    run_time=None,
                    progress_reporting=None,
                    global_models={},
                )

        self.assertEqual(len(created), 4)
        for runner in created:
            runner.shutdown.assert_called_once()
        # Pools are built in stage order, so created[2] is the sqlexec pool.
        # Its queued work holds DB connections that only SQLExecWork.run
        # returns, so it must not be cancelled.
        created[2].shutdown.assert_called_once_with(cancel_futures=False)


if __name__ == "__main__":
    unittest.main()
