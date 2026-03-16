import time
from queue import Queue
from unittest.mock import MagicMock
from work.sqlexecwork import SQLExecWork
import unittest


class TestExecutionBugs(unittest.TestCase):

    def test_sqlexecwork_handles_empty_query_safely(self):
        db = MagicMock()
        db_queue = Queue()
        eval_result = {
            "sql_generator_error": None,
            "generated_sql": "   ",
            "query_type": "dql",
            "eval_query": [],
            "golden_sql": "",
            "preprocess_sql": []
        }
        config = {
            "prompt_generator": "NOOPGenerator",
            "dialect": "sqlite"
        }

        work = SQLExecWork(db, config, eval_result, db_queue)

        # Should not raise "list index out of range"
        result = work.run()

        self.assertIsNone(result.get("generated_result"))
        self.assertEqual(
            result.get("generated_error"),
            "list index out of range (empty query)")


if __name__ == '__main__':
    unittest.main()
