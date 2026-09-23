import unittest
from queue import Queue
from unittest.mock import MagicMock

from work.sqlexecwork import SQLExecWork


def make_work(eval_result):
    db = MagicMock()
    db.dialect = "sqlite"

    def execute(query, *args, **kwargs):
        # The real db.execute starts with query.strip(), so a list raises.
        query.strip()
        return None, None, None

    db.execute.side_effect = execute
    db.get_metadata.return_value = {}
    return SQLExecWork(db, {}, eval_result, Queue()), db


def executed(db):
    return [call.args[0] for call in db.execute.call_args_list]


class TestResolveSQL(unittest.TestCase):

    def test_dml_setup_and_cleanup_lists_are_unwrapped(self):
        """copy_for_dialect leaves these as lists; db.execute needs a str."""
        work, db = make_work({
            "query_type": "dml",
            "setup_sql": ["UPDATE t SET a = 1;"],
            "cleanup_sql": ["UPDATE t SET a = 2;"],
        })

        work._evaluate_execution_results(
            "UPDATE t SET a = 3;", None, None, "dml")

        self.assertEqual(
            executed(db),
            ["UPDATE t SET a = 1;", "UPDATE t SET a = 3;",
             "UPDATE t SET a = 2;"],
        )

    def test_dml_empty_setup_list_is_skipped(self):
        work, db = make_work({
            "query_type": "dml",
            "setup_sql": [],
            "cleanup_sql": [],
        })

        work._evaluate_execution_results(
            "UPDATE t SET a = 3;", None, None, "dml")

        self.assertEqual(executed(db), ["UPDATE t SET a = 3;"])

    def test_dialect_dict_wrapping_a_list_is_unwrapped(self):
        work, db = make_work({
            "query_type": "ddl",
            "setup_sql": {"sqlite": ["ALTER TABLE t ADD COLUMN b;"]},
            "cleanup_sql": {"sqlite": ["ALTER TABLE t DROP COLUMN b;"]},
        })

        work._evaluate_execution_results(
            "ALTER TABLE t ADD COLUMN c;", None, None, "ddl")

        self.assertEqual(
            executed(db),
            ["ALTER TABLE t ADD COLUMN b;", "ALTER TABLE t ADD COLUMN c;",
             "ALTER TABLE t DROP COLUMN b;"],
        )

    def test_ddl_plain_strings_still_work(self):
        work, db = make_work({
            "query_type": "ddl",
            "setup_sql": "ALTER TABLE t ADD COLUMN b;",
            "cleanup_sql": None,
        })

        work._evaluate_execution_results(
            "ALTER TABLE t ADD COLUMN c;", None, None, "ddl")

        self.assertEqual(
            executed(db),
            ["ALTER TABLE t ADD COLUMN b;", "ALTER TABLE t ADD COLUMN c;"],
        )


if __name__ == "__main__":
    unittest.main()
