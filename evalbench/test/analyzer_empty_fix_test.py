"""Unit tests for empty evaluation handling and boundary contract enforcement."""

import unittest
from unittest.mock import MagicMock
from queue import Queue
import pandas as pd

from reporting.analyzer import analyze_one_metric, analyze_result
from scorers.setmatcher import SetMatcher
from scorers.llmrater import LLMRater
from work.sqlexecwork import validate_and_normalize_execution_result, SQLExecWork


class TestAnalyzerEmptyEvaluation(unittest.TestCase):

    def test_empty_dataframe_does_not_raise_key_error(self):
        df_empty = pd.DataFrame()
        res = analyze_one_metric(df_empty, "llmrater", 100, num_prompts=10)
        self.assertEqual(res["metric_name"], "llmrater")
        self.assertEqual(res["correct_results_count"], 0)
        self.assertEqual(res["total_results_count"], 10)

    def test_dataframe_missing_score_column(self):
        df = pd.DataFrame([{"comparator": "llmrater", "prompt_id": "p1"}])
        res = analyze_one_metric(df, "llmrater", 100, num_prompts=5)
        self.assertEqual(res["correct_results_count"], 0)
        self.assertEqual(res["total_results_count"], 5)

    def test_analyze_result_with_empty_scores_list(self):
        scores_df, summary_df = analyze_result(
            [], {"scorers": {"llmrater": {}}}, num_prompts=10
        )
        self.assertEqual(len(summary_df), 2)  # llmrater and executable
        llmrater_summary = summary_df[summary_df["metric_name"] == "llmrater"].iloc[0]
        self.assertEqual(llmrater_summary["correct_results_count"], 0)
        self.assertEqual(llmrater_summary["total_results_count"], 10)


class TestBoundaryContractEnforcement(unittest.TestCase):

    def test_normalize_none_returns_empty_list(self):
        self.assertEqual(validate_and_normalize_execution_result(None, "TestDB"), [])

    def test_normalize_empty_list_returns_empty_list(self):
        self.assertEqual(validate_and_normalize_execution_result([], "TestDB"), [])

    def test_normalize_valid_dict_rows(self):
        rows = [{"col1": 1, "col2": "a"}, {"col1": 2, "col2": "b"}]
        self.assertEqual(validate_and_normalize_execution_result(rows, "TestDB"), rows)

    def test_normalize_tuple_rows_raises_type_error(self):
        tuple_rows = [(1, "a"), (2, "b")]
        with self.assertRaises(TypeError) as ctx:
            validate_and_normalize_execution_result(tuple_rows, "CustomEngine")
        self.assertIn("CustomEngine", str(ctx.exception))
        self.assertIn("tuple", str(ctx.exception))
        self.assertIn("dict mapping column name to value", str(ctx.exception))

    def test_normalize_invalid_result_type_raises_type_error(self):
        with self.assertRaises(TypeError) as ctx:
            validate_and_normalize_execution_result(12345, "CustomEngine")
        self.assertIn("CustomEngine", str(ctx.exception))
        self.assertIn("int", str(ctx.exception))

    def test_sqlexecwork_catches_type_error_and_records_generated_error(self):
        db = MagicMock()
        db.__class__.__name__ = "BadCustomConnector"
        # Connector incorrectly returning tuples
        db.execute.return_value = ([(1, "val")], None, None)
        db_queue = Queue()
        eval_result = {
            "sql_generator_error": None,
            "generated_sql": "SELECT 1, 'val'",
            "query_type": "dql",
            "eval_query": [],
            "golden_sql": "",
            "preprocess_sql": [],
        }
        config = {"prompt_generator": "NOOPGenerator", "dialect": "sqlite"}
        work = SQLExecWork(db, config, eval_result, db_queue)
        res = work.run()
        self.assertIsNone(res.get("generated_result"))
        self.assertIn("Each row must be a dict", res.get("generated_error", ""))
        self.assertIn("BadCustomConnector", res.get("generated_error", ""))

    def test_sqlexecwork_normalizes_none_results_on_success(self):
        db = MagicMock()
        db.execute.return_value = (None, None, None)
        db_queue = Queue()
        eval_result = {
            "sql_generator_error": None,
            "generated_sql": "SELECT 1",
            "query_type": "dql",
            "eval_query": [],
            "golden_sql": "SELECT 1",
            "preprocess_sql": [],
        }
        config = {"prompt_generator": "NOOPGenerator", "dialect": "sqlite"}
        work = SQLExecWork(db, config, eval_result, db_queue)
        res = work.run()
        self.assertEqual(res.get("generated_result"), [])
        self.assertEqual(res.get("golden_result"), [])
        self.assertIsNone(res.get("generated_error"))
        self.assertIsNone(res.get("golden_error"))

    def test_sqlexecwork_ddl_setup_failure_preserves_context(self):
        db = MagicMock()
        db.execute.side_effect = RuntimeError("DDL setup connection dropped")
        db_queue = Queue()
        eval_result = {
            "sql_generator_error": None,
            "generated_sql": "CREATE TABLE t (id INT)",
            "query_type": "ddl",
            "eval_query": [],
            "golden_sql": "",
            "setup_sql": ["CREATE TABLE base (id INT)"],
            "preprocess_sql": [],
        }
        config = {"prompt_generator": "NOOPGenerator", "dialect": "sqlite"}
        work = SQLExecWork(db, config, eval_result, db_queue)
        res = work.run()
        self.assertIsNone(res.get("generated_result"))
        self.assertIn("Was not able to run DDL due to setup_error", res.get("generated_error", ""))
        self.assertIn("DDL setup connection dropped", res.get("generated_error", ""))

    def test_sqlexecwork_dml_setup_failure_preserves_context_and_skips_cleanup(self):
        db = MagicMock()
        db.execute.side_effect = RuntimeError("DML setup connection dropped")
        db_queue = Queue()
        eval_result = {
            "id": "item_dml_fail",
            "sql_generator_error": None,
            "generated_sql": "INSERT INTO t VALUES (1)",
            "query_type": "dml",
            "eval_query": ["SELECT * FROM t"],
            "golden_sql": "",
            "setup_sql": ["CREATE TABLE t (id INT)"],
            "cleanup_sql": ["DROP TABLE t"],
            "preprocess_sql": [],
        }
        config = {"prompt_generator": "NOOPGenerator", "dialect": "sqlite"}
        work = SQLExecWork(db, config, eval_result, db_queue)
        res = work.run()
        self.assertIsNone(res.get("generated_result"))
        self.assertIn("DML setup_sql failed", res.get("generated_error", ""))
        self.assertIn("DML setup connection dropped", res.get("generated_error", ""))
        self.assertEqual(db.execute.call_count, 1)
        db.execute.assert_called_once_with("CREATE TABLE t (id INT)")

    def test_sqlexecwork_ddl_setup_failure_skips_cleanup(self):
        db = MagicMock()
        db.execute.side_effect = RuntimeError("DDL setup connection dropped")
        db_queue = Queue()
        eval_result = {
            "id": "item_ddl_fail",
            "sql_generator_error": None,
            "generated_sql": "CREATE TABLE t (id INT)",
            "query_type": "ddl",
            "eval_query": [],
            "golden_sql": "",
            "setup_sql": ["CREATE TABLE base (id INT)"],
            "cleanup_sql": ["DROP TABLE base"],
            "preprocess_sql": [],
        }
        config = {"prompt_generator": "NOOPGenerator", "dialect": "sqlite"}
        work = SQLExecWork(db, config, eval_result, db_queue)
        res = work.run()
        self.assertIsNone(res.get("generated_result"))
        self.assertIn("Was not able to run DDL due to setup_error", res.get("generated_error", ""))
        self.assertEqual(db.execute.call_count, 1)
        db.execute.assert_called_once_with("CREATE TABLE base (id INT)")

    def test_sqlexecwork_cleanup_failure_is_logged_without_masking_result(self):
        db = MagicMock()
        db.execute.side_effect = [
            None,
            ([{"count": 1}], None, None),
            RuntimeError("Cleanup drop failed"),
        ]
        db_queue = Queue()
        eval_result = {
            "id": "item_cleanup_fail",
            "sql_generator_error": None,
            "generated_sql": "INSERT INTO t VALUES (1)",
            "query_type": "dml",
            "eval_query": ["SELECT count(*) FROM t"],
            "golden_sql": "",
            "setup_sql": ["CREATE TABLE t (id INT)"],
            "cleanup_sql": ["DROP TABLE t"],
            "preprocess_sql": [],
        }
        config = {"prompt_generator": "NOOPGenerator", "dialect": "sqlite"}
        work = SQLExecWork(db, config, eval_result, db_queue)
        with self.assertLogs("root", level="WARNING") as cm:
            res = work.run()
        self.assertEqual(res.get("generated_result"), [{"count": 1}])
        self.assertIsNone(res.get("generated_error"))
        self.assertTrue(any("cleanup_sql failed" in log and "item_cleanup_fail" in log for log in cm.output))


class TestScorersContractCompliance(unittest.TestCase):

    def test_setmatcher_handles_matching_and_non_matching_dicts(self):
        matcher = SetMatcher({})
        score, err = matcher.compare(
            nl_prompt="q", golden_query="", query_type="dql",
            golden_execution_result=[{"a": 1}], golden_eval_result="", golden_error=None,
            generated_query="", generated_execution_result=[{"a": 1}], generated_eval_result="",
            generated_error=None,
        )
        self.assertEqual(score, 100)

        score, err = matcher.compare(
            nl_prompt="q", golden_query="", query_type="dql",
            golden_execution_result=[{"a": 1}], golden_eval_result="", golden_error=None,
            generated_query="", generated_execution_result=[{"a": 2}], generated_eval_result="",
            generated_error=None,
        )
        self.assertEqual(score, 0)

    def test_setmatcher_handles_empty_results(self):
        matcher = SetMatcher({})
        score, err = matcher.compare(
            nl_prompt="q", golden_query="", query_type="dql",
            golden_execution_result=[], golden_eval_result="", golden_error=None,
            generated_query="", generated_execution_result=[], generated_eval_result="",
            generated_error=None,
        )
        self.assertEqual(score, 100)

    def test_llmrater_take_n_uniques(self):
        self.assertEqual(LLMRater.take_n_uniques(None, 5), [])
        dict_rows = [{"a": 1}, {"a": 1}, {"a": 2}]
        uniques = LLMRater.take_n_uniques(dict_rows, 5)
        self.assertEqual(len(uniques), 2)


if __name__ == "__main__":
    unittest.main()
