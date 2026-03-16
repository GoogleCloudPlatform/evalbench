import time
from queue import Queue
from unittest.mock import MagicMock, patch
from util.rate_limit import rate_limit, ResourceExhaustedError
from work.sqlexecwork import SQLExecWork
import unittest


class TestStability(unittest.TestCase):

    def test_rate_limit_guaranteed_release(self):
        semaphore = MagicMock()
        execution_method = MagicMock(side_effect=Exception("Execution failed"))

        # When execution_method raises an exception, the semaphore MUST still
        # be released.
        with self.assertRaises(Exception) as cm:
            rate_limit(
                query=("SELECT 1",),
                execution_method=execution_method,
                execs_per_minute=60,
                semaphore=semaphore,
                max_attempts=1
            )

        self.assertEqual(str(cm.exception), "Execution failed")
        semaphore.release.assert_called_once()

    def test_sqlexecwork_guaranteed_queue_return(self):
        db = MagicMock()
        db_queue = Queue()
        eval_result = {
            "sql_generator_error": "Some error",
            "query_type": "dql"}

        work = SQLExecWork(db, {}, eval_result, db_queue)

        # Mock _run_inner to raise an exception
        work._run_inner = MagicMock(side_effect=RuntimeError("Inner crash"))

        with self.assertRaises(RuntimeError):
            work.run()

        # The DB object MUST have been returned to the queue despite the crash
        self.assertEqual(db_queue.get_nowait(), db)


if __name__ == '__main__':
    unittest.main()
