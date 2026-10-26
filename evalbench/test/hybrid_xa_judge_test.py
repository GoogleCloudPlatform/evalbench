from decimal import Decimal
import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from scorers.judges.hybrid_xa_judge import compare_result_sets, main


class TestHybridXaJudge(unittest.TestCase):

    def test_compare_result_sets_handles_decimal_vs_float(self):
        # Decimal vs Float value cell comparison.
        df_bq = pd.DataFrame(
            [{"val": Decimal("10.05")}, {"val": Decimal("20.10")}], dtype=object
        )
        df_sqlite = pd.DataFrame([{"val": 10.05}, {"val": 20.1}], dtype=object)

        self.assertTrue(compare_result_sets(df_bq, df_sqlite))

    def test_compare_result_sets_ignores_column_names_and_row_order(self):
        # Dataframe BQ: columns [a, b], rows in order [1, 'Alice'], [2, 'Bob'].
        df_bq = pd.DataFrame([{"a": 1, "b": "Alice"}, {"a": 2, "b": "Bob"}])

        # Dataframe SQLite: different columns [x, y], shuffled rows
        # [2, 'Bob'], [1, 'Alice'].
        df_sqlite = pd.DataFrame([{"x": 2, "y": "Bob"}, {"x": 1, "y": "Alice"}])

        self.assertTrue(compare_result_sets(df_bq, df_sqlite))

    def test_compare_result_sets_different_row_lengths(self):
        df_bq = pd.DataFrame([{"a": 1}, {"a": 2}])
        df_sqlite = pd.DataFrame([{"a": 1}])

        self.assertFalse(compare_result_sets(df_bq, df_sqlite))

    def test_compare_result_sets_rounding(self):
        # Tests that floats are rounded to 4 decimal places.
        df_bq = pd.DataFrame([{"val": 1.123456}])
        df_sqlite = pd.DataFrame([{"val": 1.1235}])

        self.assertTrue(compare_result_sets(df_bq, df_sqlite))

    @patch("scorers.judges.hybrid_xa_judge.get_sqlite_ground_truth")
    def test_hybrid_xa_judge_main_with_matching_results(self, mock_sqlite_gt):
        input_data = {
            "database": "mock_db",
            "golden_query": "SELECT * FROM users",
            "generated_execution_result": [{"id": 1, "name": "Alice"}],
            "generated_error": None,
            "sqlite_db_dir": "/dummy/path",
        }

        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            mock_sqlite_gt.return_value = [{"id": 1, "name": "Alice"}]
            main()
            out_data = json.loads(mock_stdout.getvalue().strip())
            self.assertEqual(out_data["score"], 100.0)
            self.assertIn("PASS", out_data["reason"])

    def test_hybrid_xa_judge_main_keeps_columns_that_share_a_name(self):
        # The golden query returns two columns named "name": a prediction that
        # carries both must pass, and one that drops a column must fail.
        with tempfile.TemporaryDirectory() as db_dir:
            conn = sqlite3.connect(os.path.join(db_dir, "f1.sqlite"))
            conn.execute("CREATE TABLE users (id INT, name TEXT)")
            conn.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
            conn.commit()
            conn.close()
            for prediction, expected_score in [
                ([{"first": "Alice", "second": "Bob"}], 100.0),
                ([{"name": "Bob"}], 0.0),
            ]:
                input_data = {
                    "database": "f1",
                    "golden_query": (
                        "SELECT a.name, b.name FROM users a "
                        "JOIN users b ON b.id = a.id + 1"
                    ),
                    "generated_execution_result": prediction,
                    "generated_error": None,
                    "sqlite_db_dir": db_dir,
                }
                with (
                    self.subTest(prediction=prediction),
                    patch("sys.stdin", io.StringIO(json.dumps(input_data))),
                    patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
                ):
                    main()
                    out_data = json.loads(mock_stdout.getvalue().strip())
                    self.assertEqual(out_data["score"], expected_score)


if __name__ == "__main__":
    unittest.main()
