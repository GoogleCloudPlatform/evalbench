"""Tests for the dedicated evaluation executor."""

import asyncio
import os
import threading
import unittest
from unittest.mock import patch

from util import eval_executor
from util.eval_executor import EvalExecutor, size_from_env


class TestSizeFromEnv(unittest.TestCase):

    def test_unset_uses_the_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(size_from_env("X_THREADS", 7), 7)

    def test_reads_a_positive_integer(self):
        with patch.dict(os.environ, {"X_THREADS": "128"}):
            self.assertEqual(size_from_env("X_THREADS", 7), 128)

    def test_rejects_garbage_and_non_positive_values(self):
        for bad in ("lots", "0", "-3"):
            with patch.dict(os.environ, {"X_THREADS": bad}):
                self.assertEqual(size_from_env("X_THREADS", 7), 7, bad)


class TestEvalExecutor(unittest.TestCase):

    def test_runs_work_off_the_loop_thread(self):
        executor = EvalExecutor("t", 2)
        self.addCleanup(executor.shutdown)

        async def run():
            return await executor.run(threading.current_thread)

        worker = asyncio.run(run())
        self.assertIsNot(worker, threading.main_thread())
        self.assertTrue(worker.name.startswith("t"))

    def test_more_than_32_evaluations_run_at_once(self):
        # The point of the dedicated pool: the default executor stops at
        # min(32, cpu + 4) no matter how much work is idle-waiting.
        n = 40
        executor = EvalExecutor("t", n)
        self.addCleanup(executor.shutdown)
        all_started = threading.Barrier(n, timeout=10)

        async def run():
            await asyncio.gather(*(executor.run(all_started.wait) for _ in range(n)))

        asyncio.run(run())  # BrokenBarrierError if fewer than n ran together

    def test_does_not_starve_to_thread(self):
        # Interact polls its queue with asyncio.to_thread while its evaluation
        # holds a thread. With a shared, saturated pool that poll never ran.
        executor = EvalExecutor("t", 1)
        self.addCleanup(executor.shutdown)
        release = threading.Event()

        async def run():
            held = executor.run(release.wait, 10)
            polled = await asyncio.wait_for(asyncio.to_thread(lambda: "polled"), 5)
            release.set()
            await held
            return polled

        self.assertEqual(asyncio.run(run()), "polled")

    def test_tracks_in_flight_and_warns_when_queued(self):
        executor = EvalExecutor("t", 1)
        self.addCleanup(executor.shutdown)
        release = threading.Event()

        async def run():
            first = executor.run(release.wait, 10)
            with self.assertLogs(level="WARNING") as logs:
                second = executor.run(lambda: None)
            self.assertEqual(executor.in_flight, 2)
            release.set()
            await asyncio.gather(first, second)
            return logs.output

        output = asyncio.run(run())
        self.assertIn("queued", output[0])
        self.assertEqual(executor.in_flight, 0)

    def test_in_flight_is_released_when_work_raises(self):
        executor = EvalExecutor("t", 1)
        self.addCleanup(executor.shutdown)

        def boom():
            raise RuntimeError("boom")

        async def run():
            with self.assertRaises(RuntimeError):
                await executor.run(boom)

        asyncio.run(run())
        self.assertEqual(executor.in_flight, 0)


class TestGetEvalExecutor(unittest.TestCase):

    def setUp(self):
        eval_executor._EVAL_EXECUTOR = None
        self.addCleanup(setattr, eval_executor, "_EVAL_EXECUTOR", None)

    def test_is_a_process_wide_singleton(self):
        self.assertIs(eval_executor.get_eval_executor(),
                      eval_executor.get_eval_executor())

    def test_is_sized_by_env(self):
        with patch.dict(os.environ, {"EVALBENCH_EVAL_THREADS": "64"}):
            self.assertEqual(eval_executor.get_eval_executor().max_workers, 64)

    def test_defaults_to_the_old_implicit_bound(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                eval_executor.get_eval_executor().max_workers,
                min(32, (os.cpu_count() or 1) + 4))


if __name__ == "__main__":
    unittest.main()
