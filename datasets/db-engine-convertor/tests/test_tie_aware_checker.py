"""Unit tests for MySQL-to-PostgreSQL query converter and tie-breaker checking.

This module contains:
  - `TestTieAwareResultComparison`: Verifies `MySQLToPGQueryConverter.compare_results`
    (Check 1: exact row-by-row match with 4-decimal rounding tolerance; Check 2:
    same exact rows across 100% of columns when sorted, with zero columns ever
    ignored or dropped so any mismatch in `AVG`, `ROW_NUMBER`, `DENSE_RANK`, or
    running totals is rejected).
  - `TestOrchestratorTieEquivalenceDefault`: Verifies that
    `QueryConversionOrchestrator` defaults `allow_tie_equivalence` to `False`
    for all existing converters while running shared tie-breaker verification
    across both queries for `MySQLToPGQueryConverter`.
  - `TestDeterministicTiebreakerInjection`: Verifies that
    `_inject_deterministic_tiebreakers` adds shared tie-breaker columns to
    `ROW_NUMBER`, `LAG`, moving-window (`ROWS BETWEEN`), and `ORDER BY ... LIMIT`
    queries while leaving `DENSE_RANK` untouched.
"""

import decimal
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db_convertor.query_conversion_orchestrator import (  # pylint: disable=g-import-not-at-top
    QueryConversionOrchestrator,
    _inject_deterministic_tiebreakers,
    _window_allows_tiebreakers,
)
from db_convertor.query_converters.base import (  # pylint: disable=g-import-not-at-top
    ConversionStatus,
    QueryResult,
)
from db_convertor.query_converters.mysql_to_pg import (  # pylint: disable=g-import-not-at-top
    MySQLToPGQueryConverter,
)
from db_convertor.query_converters.sqlite_to_pg import (  # pylint: disable=g-import-not-at-top
    SQLiteToPGQueryConverter,
)


class TestTieAwareResultComparison(unittest.TestCase):
    """Tests for `MySQLToPGQueryConverter.compare_results` (100% column matching, zero column dropping)."""

    def test_mysql_to_pg_converter_dialects_and_prompt(self):
        """Verify MySQLToPGQueryConverter initializes dialects and builds a prompt."""
        converter = MySQLToPGQueryConverter()
        self.assertEqual(converter.source_dialect, "mysql")
        self.assertEqual(converter.dest_dialect, "postgresql")
        self.assertTrue(converter.allow_tie_equivalence)
        prompt = converter.get_conversion_prompt(
            source_schema="CREATE TABLE t (id INT);",
            dest_schema='CREATE TABLE "t" ("id" INTEGER);',
            source_query="SELECT `id` FROM `t`;",
        )
        self.assertIn("SOURCE (MySQL):", prompt)
        self.assertIn("DESTINATION (PostgreSQL):", prompt)

    def test_four_decimal_mysql_rounding_matches_only_exact_rounding(self):
        """MySQL's 4-decimal AVG/DIV value matches the full value only if it is that value rounded."""
        converter = MySQLToPGQueryConverter()
        src = QueryResult(
            columns=["dept", "avg_units"],
            rows=[("Physics", "11.3333"), ("Math", "9.6667")],
            total_rows=2,
        )
        dest = QueryResult(
            columns=["dept", "avg_units"],
            rows=[("Physics", 11.333333333333334), ("Math", 9.666666666666666)],
            total_rows=2,
        )
        matches, reason = converter.compare_results(src, dest)
        self.assertTrue(matches)
        self.assertIn("data matches", reason)
        # A real difference in the 4th decimal is still a mismatch.
        dest_off = QueryResult(
            columns=["dept", "avg_units"],
            rows=[("Physics", 11.3340), ("Math", 9.666666666666666)],
            total_rows=2,
        )
        self.assertFalse(converter.compare_results(src, dest_off)[0])

    def test_same_rows_reordered_on_ties(self):
        """Verify two tables with the exact same rows across all columns in different tie orders match."""
        converter = MySQLToPGQueryConverter()
        src = QueryResult(
            columns=["term_code", "subject_id", "units"],
            rows=[
                ("2024FA", "18.01", 12),
                ("2024FA", "18.02", 12),
                ("2024FA", "18.03", 12),
            ],
            total_rows=3,
        )
        dest = QueryResult(
            columns=["term_code", "subject_id", "units"],
            rows=[
                ("2024FA", "18.03", 12),
                ("2024FA", "18.01", 12),
                ("2024FA", "18.02", 12),
            ],
            total_rows=3,
        )
        query = "SELECT term_code, subject_id, units FROM t ORDER BY term_code"
        matches, reason = converter.compare_results(src, dest, source_query=query)
        self.assertTrue(matches)
        self.assertIn("same rows reordered", reason)

        # When allow_tie_equivalence is False, reordered rows must be rejected.
        strict_converter = MySQLToPGQueryConverter(allow_tie_equivalence=False)
        strict_matches, _ = strict_converter.compare_results(
            src, dest, source_query=query
        )
        self.assertFalse(strict_matches)

        # Without the source query the order cannot be checked, so reject.
        self.assertFalse(converter.compare_results(src, dest)[0])

    def test_no_column_dropping_rejects_any_single_column_mismatch(self):
        """Verify compare_results never drops columns and rejects any single-column mismatch."""
        converter = MySQLToPGQueryConverter()
        src_avg = QueryResult(
            columns=["dept", "cnt", "avg_half"],
            rows=[("EECS", 4, 5.5), ("Physics", 3, 4.5)],
            total_rows=2,
        )
        dest_avg = QueryResult(
            columns=["dept", "cnt", "avg_half"],
            rows=[("EECS", 4, 5.0), ("Physics", 3, 4.0)],
            total_rows=2,
        )
        self.assertFalse(converter.compare_results(src_avg, dest_avg)[0])

    def test_avg_mismatch_alongside_row_number_is_rejected(self):
        """Verify an AVG integer-division bug alongside a ROW_NUMBER column is rejected."""
        converter = MySQLToPGQueryConverter()
        src = QueryResult(
            columns=["dept", "avg_val", "rn"],
            rows=[("EECS", 5.5, 1), ("Physics", 4.5, 2)],
            total_rows=2,
        )
        dest = QueryResult(
            columns=["dept", "avg_val", "rn"],
            rows=[("EECS", 5.0, 1), ("Physics", 4.0, 2)],
            total_rows=2,
        )
        self.assertFalse(converter.compare_results(src, dest)[0])

    def test_unresolved_window_column_mismatch_is_rejected_by_compare_results(self):
        """Verify compare_results rejects an unresolved ROW_NUMBER difference unless resolved by tie-breakers."""
        converter = MySQLToPGQueryConverter()
        src_win = QueryResult(
            columns=["dept", "course", "rn"],
            rows=[("EECS", "6.001", 1), ("EECS", "6.002", 2)],
            total_rows=2,
        )
        dest_win = QueryResult(
            columns=["dept", "course", "rn"],
            rows=[("EECS", "6.002", 1), ("EECS", "6.001", 2)],
            total_rows=2,
        )
        self.assertFalse(converter.compare_results(src_win, dest_win)[0])

    def test_dense_rank_collation_mismatch_is_rejected(self):
        """Verify a DENSE_RANK difference caused by sort-order/collation differences is rejected."""
        converter = MySQLToPGQueryConverter()
        src = QueryResult(
            columns=["code", "dr"],
            rows=[("A-1", 1), ("A 2", 2), ("A-3", 3)],
            total_rows=3,
        )
        dest = QueryResult(
            columns=["code", "dr"],
            rows=[("A 2", 1), ("A-1", 2), ("A-3", 3)],
            total_rows=3,
        )
        self.assertFalse(converter.compare_results(src, dest)[0])

    def test_unframed_running_sum_mismatch_is_rejected(self):
        """Verify a running-total SUM OVER (ORDER BY ...) difference is rejected."""
        converter = MySQLToPGQueryConverter()
        src = QueryResult(
            columns=["code", "running_total"],
            rows=[("A", 10), ("B", 30)],
            total_rows=2,
        )
        dest = QueryResult(
            columns=["code", "running_total"],
            rows=[("A", 20), ("B", 30)],
            total_rows=2,
        )
        self.assertFalse(converter.compare_results(src, dest)[0])

    def test_top1_sort_key_aggregate_mismatch_is_rejected(self):
        """Verify a top-1 query where the sort-key aggregate (e.g. COUNT) differs is rejected."""
        converter = MySQLToPGQueryConverter()
        src = QueryResult(
            columns=["dept", "top_course", "student_count"],
            rows=[("EECS", "6.001", 120)],
            total_rows=1,
        )
        dest = QueryResult(
            columns=["dept", "top_course", "student_count"],
            rows=[("EECS", "6.002", 95)],
            total_rows=1,
        )
        self.assertFalse(converter.compare_results(src, dest)[0])

    def test_genuine_base_row_mismatch_is_rejected(self):
        """Verify queries returning different rows fail."""
        converter = MySQLToPGQueryConverter()
        src = QueryResult(
            columns=["dept", "course", "moving_avg"],
            rows=[("EECS", "6.001", 10.0), ("EECS", "6.002", 12.0)],
            total_rows=2,
        )
        dest = QueryResult(
            columns=["dept", "course", "moving_avg"],
            rows=[("EECS", "6.001", 10.0), ("Physics", "8.01", 12.0)],
            total_rows=2,
        )
        matches, _ = converter.compare_results(src, dest)
        self.assertFalse(matches)


def _result(columns, rows):
    """Builds a QueryResult from column names and rows."""
    return QueryResult(columns=columns, rows=list(rows), total_rows=len(rows))


class TestRowOrderIsChecked(unittest.TestCase):
    """Tests that Check 2 only lets rows move within ORDER BY ties."""

    def setUp(self):
        """Creates the converter under test."""
        self.converter = MySQLToPGQueryConverter()

    def _matches(self, query, src_rows, dest_rows, columns=("name", "n")):
        """Returns whether `compare_results` accepts the two results."""
        return self.converter.compare_results(
            _result(list(columns), src_rows),
            _result(list(columns), dest_rows),
            source_query=query,
        )[0]

    def test_reversed_sort_is_rejected(self):
        """DESC in MySQL vs ASC in PostgreSQL, with no ties, does not match."""
        self.assertFalse(
            self._matches(
                "SELECT name, n FROM t ORDER BY n DESC",
                [("a", 3), ("b", 2), ("c", 1)],
                [("c", 1), ("b", 2), ("a", 3)],
            )
        )

    def test_tied_rows_may_swap(self):
        """Rows tied on the sort key may come back in either order."""
        self.assertTrue(
            self._matches(
                "SELECT name, n FROM t ORDER BY n DESC",
                [("a", 3), ("b", 2), ("c", 2), ("d", 1)],
                [("a", 3), ("c", 2), ("b", 2), ("d", 1)],
            )
        )

    def test_rows_swapped_across_a_tie_group_are_rejected(self):
        """A row may not jump into another group of tied rows."""
        self.assertFalse(
            self._matches(
                "SELECT name, n FROM t ORDER BY n DESC",
                [("a", 3), ("b", 2), ("c", 2), ("d", 1)],
                [("b", 2), ("a", 3), ("c", 2), ("d", 1)],
            )
        )

    def test_wrong_second_sort_key_is_rejected(self):
        """Same first key, but the second key sorted the other way."""
        self.assertFalse(
            self._matches(
                "SELECT name, n FROM t ORDER BY n DESC, name ASC",
                [("a", 2), ("b", 2), ("c", 1)],
                [("b", 2), ("a", 2), ("c", 1)],
            )
        )

    def test_no_order_by_accepts_any_order(self):
        """Without a top-level ORDER BY, any row order is fine."""
        self.assertTrue(
            self._matches(
                "SELECT name, n FROM (SELECT * FROM t ORDER BY n) x",
                [("a", 3), ("b", 2)],
                [("b", 2), ("a", 3)],
            )
        )

    def test_order_by_in_window_or_subquery_is_not_the_final_order(self):
        """ORDER BY inside OVER (...) does not sort the final result."""
        query = "SELECT name, ROW_NUMBER() OVER (ORDER BY name) AS n FROM t"
        self.assertTrue(
            self._matches(query, [("a", 1), ("b", 2)], [("b", 2), ("a", 1)])
        )

    def test_sort_key_that_is_not_an_output_column_is_rejected(self):
        """The order cannot be checked if the key is not in the result."""
        self.assertFalse(
            self._matches(
                "SELECT name, n FROM t ORDER BY created_at",
                [("a", 1), ("b", 2)],
                [("b", 2), ("a", 1)],
            )
        )

    def test_keys_by_position_alias_and_expression(self):
        """ORDER BY 2, an alias and a SELECT expression all map to columns."""
        src = [("a", 3), ("b", 2), ("c", 2)]
        dest_ok = [("a", 3), ("c", 2), ("b", 2)]
        dest_bad = [("b", 2), ("c", 2), ("a", 3)]
        for query in (
            "SELECT name, n FROM t ORDER BY 2 DESC",
            "SELECT name, cnt AS n FROM t ORDER BY n DESC",
            "SELECT name, COUNT(*) AS n FROM t GROUP BY name ORDER BY COUNT(*) DESC",
            "SELECT `t`.`name`, `t`.`n` FROM t ORDER BY `t`.`n` DESC",
        ):
            with self.subTest(query=query):
                self.assertTrue(self._matches(query, src, dest_ok))
                self.assertFalse(self._matches(query, src, dest_bad))

    def test_null_placement_difference_is_rejected(self):
        """MySQL puts NULLs first on ASC; PostgreSQL's default puts them last."""
        self.assertFalse(
            self._matches(
                "SELECT name, n FROM t ORDER BY n",
                [("x", None), ("a", 1), ("b", 2)],
                [("a", 1), ("b", 2), ("x", None)],
            )
        )

    def test_text_order_must_follow_mysql(self):
        """'apple' before 'Banana' (MySQL) vs 'Banana' first ("C" order)."""
        self.assertFalse(
            self._matches(
                "SELECT name, n FROM t ORDER BY name",
                [("apple", 1), ("Banana", 2)],
                [("Banana", 2), ("apple", 1)],
            )
        )

    def test_case_only_differences_count_as_ties_for_ordering(self):
        """MySQL treats 'apple' and 'Apple' as tied, so they may swap."""
        self.assertTrue(
            self._matches(
                "SELECT name, n FROM t ORDER BY name",
                [("apple", 1), ("Apple", 2), ("berry", 3)],
                [("Apple", 2), ("apple", 1), ("berry", 3)],
            )
        )

    def test_order_by_text_in_quotes_or_comments_is_ignored(self):
        """'ORDER BY' inside a string or comment is not the query's ORDER BY."""
        query = (
            "SELECT name, n FROM t WHERE note = 'order by n' "
            "-- order by name\n"
        )
        self.assertTrue(
            self._matches(query, [("a", 1), ("b", 2)], [("b", 2), ("a", 1)])
        )


class TestValueRules(unittest.TestCase):
    """Tests how single cell values are compared."""

    def setUp(self):
        """Creates the converter under test."""
        self.converter = MySQLToPGQueryConverter()

    def test_whole_numbers_must_match_exactly(self):
        """IDs, counts and integer sums may not differ at all."""
        eq = self.converter._values_equal  # pylint: disable=protected-access
        self.assertFalse(eq(123456, 123460))
        self.assertFalse(eq(decimal.Decimal("12345678"), 12346900))
        self.assertTrue(eq(decimal.Decimal("12345678"), 12345678))
        self.assertFalse(eq(9007199254740993, 9007199254740992))
        self.assertTrue(eq(9007199254740993, 9007199254740993))

    def test_decimals_within_1e9_or_exactly_rounded_by_mysql(self):
        """Non-whole numbers agree within 1e-9, or MySQL's value is the PG value rounded."""
        eq = self.converter._values_equal  # pylint: disable=protected-access
        # Exactly MySQL's rounding of the PostgreSQL value.
        self.assertTrue(eq(decimal.Decimal("11.3333"), 11.333333333333334))
        self.assertTrue(eq(decimal.Decimal("9.6667"), decimal.Decimal("9.66666666667")))
        self.assertTrue(eq(decimal.Decimal("-2.3457"), -2.34565))
        # Off in the last shown digit, so not a rounding of it.
        self.assertFalse(eq(decimal.Decimal("11.3333"), 11.3340))
        self.assertFalse(eq(decimal.Decimal("11.3333"), 11.33336))
        # Only the MySQL side may be the rounded one.
        self.assertFalse(eq(11.333333333333334, decimal.Decimal("11.3333")))
        # A whole MySQL number is never matched by rounding.
        self.assertFalse(eq(decimal.Decimal("12"), 12.4))
        # MySQL floats have no "shown decimals"; a 0.4% difference fails.
        self.assertFalse(eq(-0.8394717261835467, -0.8428571428084979))
        self.assertTrue(eq(decimal.Decimal("5.0000"), 5.0))
        self.assertTrue(eq(decimal.Decimal("2.5"), 2.5))
        self.assertTrue(eq(0.1 + 0.2, 0.3))
        self.assertFalse(eq(2.5, 2.0))

    def test_tolerance_is_relative_to_the_size_of_the_number(self):
        """Large numbers may differ in their 16th digit; small ones may not move."""
        eq = self.converter._values_equal  # pylint: disable=protected-access
        self.assertTrue(eq(decimal.Decimal("16029126931.864092"), 16029126931.864094))
        self.assertTrue(eq(decimal.Decimal("1520974.2476120926"), 1520974.2476120899))
        self.assertFalse(eq(decimal.Decimal("1520974.24"), 1520974.26))
        self.assertFalse(eq(decimal.Decimal("0.000001"), 0.000003))

    def test_mysql_float_values_get_one_part_in_a_million(self):
        """MySQL FLOAT values (7 digits) may differ by 1e-6; exact decimals may not."""
        eq = self.converter._values_equal  # pylint: disable=protected-access
        # Returned as a Python float by MySQL: FLOAT column leeway.
        self.assertTrue(eq(137.79042429139767, 137.79042406634449))
        self.assertFalse(eq(137.79, 137.80))
        # The same numbers as exact decimals get no extra leeway.
        self.assertFalse(
            eq(decimal.Decimal("137.79042429139767"), 137.79042406634449)
        )
        # Whole numbers stay exact even if MySQL returned a float-like int.
        self.assertFalse(eq(1000000, 1000001))

    def test_text_must_match_exactly(self):
        """Letter case, spaces and leading zeros all count."""
        eq = self.converter._values_equal  # pylint: disable=protected-access
        self.assertFalse(eq("Alice", "alice"))
        self.assertFalse(eq("a ", "a"))
        self.assertFalse(eq("007", "7"))
        self.assertFalse(eq("007", 7))
        self.assertTrue(eq("7", 7))
        self.assertTrue(eq("Alice", "Alice"))

    def test_booleans_match_zero_and_one(self):
        """MySQL returns 1/0 where PostgreSQL returns true/false."""
        eq = self.converter._values_equal  # pylint: disable=protected-access
        self.assertTrue(eq(1, True))
        self.assertTrue(eq(0, False))
        self.assertFalse(eq(1, False))

    def test_nulls(self):
        """Two NULLs match; a NULL and a value do not."""
        eq = self.converter._values_equal  # pylint: disable=protected-access
        self.assertTrue(eq(None, None))
        self.assertFalse(eq(None, 0))
        self.assertFalse(eq("", None))


class TestOrchestratorTieEquivalenceDefault(unittest.TestCase):
    """Tests that `QueryConversionOrchestrator` defaults tie equivalence to `False`."""

    def test_orchestrator_defaults_tie_equivalence_to_false_for_existing_converters(self):
        """Verify existing converters without `allow_tie_equivalence` stay strict and skip tie-breaker retries."""
        sqlite_converter = SQLiteToPGQueryConverter()
        self.assertFalse(hasattr(sqlite_converter, "allow_tie_equivalence"))

        orchestrator = QueryConversionOrchestrator(
            converter=sqlite_converter,
            source_connection={"path": ":memory:"},
            dest_connection={},
            source_schema="CREATE TABLE t (grp TEXT, v INT);",
            dest_schema='CREATE TABLE "t" ("grp" TEXT, "v" INT);',
            max_attempts=1,
        )
        source_sql = (
            "SELECT grp, v, ROW_NUMBER() OVER (ORDER BY grp) AS rn FROM t"
        )
        candidate_pg_sql = (
            'SELECT "grp", "v", ROW_NUMBER() OVER (ORDER BY "grp") AS "rn" FROM "t"'
        )

        src_res = QueryResult(
            columns=["grp", "v", "rn"],
            rows=[("A", 10, 1), ("A", 20, 2)],
            total_rows=2,
        )
        dest_res = QueryResult(
            columns=["grp", "v", "rn"],
            rows=[("A", 20, 1), ("A", 10, 2)],
            total_rows=2,
        )
        llm_reply = json.dumps({
            "converted_query": candidate_pg_sql,
            "notes": "Initial conversion",
        })

        with (
            mock.patch.object(
                orchestrator, "_execute_source_query", return_value=src_res
            ),
            mock.patch.object(
                orchestrator, "_execute_dest_query", return_value=dest_res
            ),
            mock.patch.object(
                orchestrator, "_check_shared_window_tiebreaker"
            ) as mock_tb,
            mock.patch(
                "db_convertor.query_conversion_orchestrator.gemini_inference",
                return_value=llm_reply,
            ),
        ):
            result = orchestrator.convert_query(
                source_sql, question_id="q1", verbose=False
            )
            mock_tb.assert_not_called()
            self.assertEqual(result.status, ConversionStatus.EXHAUSTED_RETRY)

    def test_compare_passes_source_query_only_to_converters_that_take_it(self):
        """The MySQL converter gets the source query; older converters do not."""
        src = _result(["n"], [(3,), (1,)])
        dest = _result(["n"], [(1,), (3,)])
        mysql_orch = QueryConversionOrchestrator(
            converter=MySQLToPGQueryConverter(),
            source_connection={},
            dest_connection={},
            source_schema="",
            dest_schema="",
        )
        # Reversed order with ORDER BY n DESC must be rejected...
        self.assertFalse(
            mysql_orch._compare(  # pylint: disable=protected-access
                src, dest, "SELECT n FROM t ORDER BY n DESC"
            )[0]
        )
        # ...but is fine when the query has no ORDER BY.
        self.assertTrue(
            mysql_orch._compare(src, dest, "SELECT n FROM t")[0]  # pylint: disable=protected-access
        )
        sqlite_orch = QueryConversionOrchestrator(
            converter=SQLiteToPGQueryConverter(),
            source_connection={"path": ":memory:"},
            dest_connection={},
            source_schema="",
            dest_schema="",
        )
        with mock.patch.object(
            sqlite_orch.converter, "compare_results", return_value=(False, "x")
        ) as mock_cmp:
            sqlite_orch._compare(src, dest, "SELECT n FROM t")  # pylint: disable=protected-access
            mock_cmp.assert_called_once_with(src, dest)

    def test_orchestrator_resolves_tie_via_deterministic_tiebreaker_on_both_queries(self):
        """Verify MySQLToPGQueryConverter runs shared deterministic tie-breakers and requires 100%-column match."""
        mysql_converter = MySQLToPGQueryConverter()
        orchestrator = QueryConversionOrchestrator(
            converter=mysql_converter,
            source_connection={},
            dest_connection={},
            source_schema="CREATE TABLE t (id INT, grp VARCHAR(10), v INT);",
            dest_schema='CREATE TABLE "t" ("id" INT, "grp" VARCHAR(10), "v" INT);',
            max_attempts=1,
        )
        source_sql = (
            "SELECT t.grp, t.v, ROW_NUMBER() OVER (ORDER BY t.grp) AS rn FROM t"
        )
        candidate_pg_sql = (
            'SELECT "t"."grp", "t"."v", ROW_NUMBER() OVER (ORDER BY "t"."grp") AS "rn" FROM "t"'
        )
        # Before tie-breaking, tied rows in grp='A' get opposite ROW_NUMBER() values.
        src_initial = QueryResult(
            columns=["grp", "v", "rn"],
            rows=[("A", 10, 1), ("A", 20, 2)],
            total_rows=2,
        )
        dest_initial = QueryResult(
            columns=["grp", "v", "rn"],
            rows=[("A", 20, 1), ("A", 10, 2)],
            total_rows=2,
        )
        # After deterministic tie-breaking on (t.grp, t.v, t.id), both queries produce identical rows across all 3 columns.
        tiebroken_aligned = QueryResult(
            columns=["grp", "v", "rn"],
            rows=[("A", 10, 1), ("A", 20, 2)],
            total_rows=2,
        )
        llm_reply = json.dumps({
            "converted_query": candidate_pg_sql,
            "notes": "Initial conversion",
        })

        with (
            mock.patch.object(
                orchestrator,
                "_execute_source_query",
                side_effect=[src_initial, tiebroken_aligned],
            ) as mock_src_exec,
            mock.patch.object(
                orchestrator,
                "_execute_dest_query",
                side_effect=[dest_initial, tiebroken_aligned],
            ) as mock_dest_exec,
            mock.patch(
                "db_convertor.query_conversion_orchestrator.gemini_inference",
                return_value=llm_reply,
            ),
        ):
            result = orchestrator.convert_query(
                source_sql, question_id="q2", verbose=False
            )
            self.assertEqual(result.status, ConversionStatus.RESULT_MATCHED)
            self.assertEqual(mock_src_exec.call_count, 2)
            self.assertEqual(mock_dest_exec.call_count, 2)
            self.assertIn("shared window tie-breaker", result.reason)


class TestDeterministicTiebreakerInjection(unittest.TestCase):
    """Tests for `_inject_deterministic_tiebreakers`."""

    def test_injects_other_select_columns_and_agg_arg_into_rows_between(self):
        """Verify the other SELECT columns and window input column are added to ORDER BY."""
        sql = (
            "SELECT s.subject_title, s.department_name, "
            "MIN(s.total_units) OVER (ORDER BY at.academic_year ASC NULLS LAST "
            "ROWS BETWEEN 3 PRECEDING AND 1 FOLLOWING) AS window_column "
            "FROM subject_summary s"
        )
        rewritten = _inject_deterministic_tiebreakers(sql)
        self.assertIn(
            "ORDER BY at.academic_year ASC NULLS LAST, s.subject_title, s.department_name, s.total_units ROWS BETWEEN",
            rewritten,
        )

    def test_leaves_dense_rank_untouched_while_breaking_ties_on_lag(self):
        """Verify DENSE_RANK() is left untouched (so tied rows keep the same rank) while LAG() gets tie-breakers."""
        sql = (
            "SELECT dept, course, "
            "DENSE_RANK() OVER (ORDER BY dept) AS dr, "
            "LAG(course) OVER (ORDER BY dept) AS prev_course "
            "FROM courses"
        )
        rewritten = _inject_deterministic_tiebreakers(sql)
        # DENSE_RANK() OVER (ORDER BY dept) must be left untouched so tied rows share the same rank.
        self.assertIn("DENSE_RANK() OVER (ORDER BY dept)", rewritten)
        # LAG(course) OVER (ORDER BY dept) must get the tie-breaker columns added.
        self.assertIn(
            "LAG(course) OVER (ORDER BY dept, dept, course, course)",
            rewritten,
        )

    def test_injects_tiebreakers_into_order_by_limit(self):
        """Verify ORDER BY ... LIMIT queries receive deterministic tie-breakers from SELECT."""
        sql = (
            "SELECT s.department_code, s.course_title "
            "FROM courses s "
            "ORDER BY s.department_code ASC LIMIT 5"
        )
        rewritten = _inject_deterministic_tiebreakers(sql, dialect="mysql")
        self.assertIn(
            "s.course_title IS NOT NULL ASC, s.course_title ASC LIMIT 5",
            rewritten,
        )

    def test_postgresql_appended_text_keys_use_byte_order(self):
        """Appended PostgreSQL text keys sort byte by byte; the query's own key is unchanged."""
        schema_ddl = 'CREATE TABLE "courses" ("id" INT, "dept" VARCHAR(20), "title" VARCHAR(100));'
        sql = (
            'SELECT c."dept", c."title", '
            'ROW_NUMBER() OVER (ORDER BY c."dept") AS rn '
            'FROM "courses" c'
        )
        rewritten = _inject_deterministic_tiebreakers(
            sql, dialect="postgresql", schema_ddl=schema_ddl
        )
        self.assertIn('ORDER BY c."dept", ', rewritten)
        self.assertIn('CAST(c."title" AS TEXT) COLLATE "C" ASC NULLS FIRST', rewritten)
        self.assertIn('"c"."id" ASC NULLS FIRST', rewritten)
        self.assertNotIn("UPPER(", rewritten)

    def test_postgresql_text_key_on_number_column_still_runs(self):
        """A helper column wrongly guessed to be text (`min_instance_id`) is cast to text, not given COLLATE directly."""
        schema_ddl = 'CREATE TABLE "instances" ("id" INT, "instance_id" VARCHAR(36));'
        sql = (
            'SELECT ic."name", ic."min_instance_id", '
            'LAG(ic."name") OVER (ORDER BY ic."name") AS prev '
            'FROM ic_table ic'
        )
        rewritten = _inject_deterministic_tiebreakers(
            sql, dialect="postgresql", schema_ddl=schema_ddl
        )
        self.assertIn('CAST(ic."min_instance_id" AS TEXT) COLLATE "C"', rewritten)
        self.assertNotIn('ic."min_instance_id" COLLATE', rewritten)

    def test_repeated_helper_table_name_columns_are_not_used(self):
        """Two `WITH inner_cte` blocks with different columns: neither one's columns are borrowed."""
        sql = (
            "WITH a AS (WITH inner_cte AS (SELECT t.x, t.y FROM t) "
            "SELECT x, ROW_NUMBER() OVER (ORDER BY x) AS rn FROM inner_cte), "
            "b AS (WITH inner_cte AS (SELECT u.z FROM u) SELECT z FROM inner_cte) "
            "SELECT * FROM a JOIN b ON a.x = b.z"
        )
        rewritten = _inject_deterministic_tiebreakers(sql, dialect="mysql")
        # Before the fix, `inner_cte.z` (from the second definition) was added
        # to the first block, where no such column exists.
        self.assertNotIn("inner_cte.z", rewritten)
        self.assertNotIn("inner_cte.y", rewritten)
        self.assertIn("ROW_NUMBER() OVER (ORDER BY x, x IS NOT NULL ASC, x ASC)", rewritten)



_SQLITE_ROWS = """
CREATE TABLE emp (id INT, dept TEXT, name TEXT, sal INT, d TEXT);
INSERT INTO emp VALUES
  (1,'A','zed',100,'2024-01-01'), (2,'A','amy',100,'2024-01-01'),
  (3,'A','bob',90,'2024-01-02'), (4,'B','cat',80,'2024-01-01'),
  (5,'B','dan',80,'2024-01-03'), (6,'B','eve',70,'2024-01-03');
"""
_EMP_DDL = "CREATE TABLE emp (id INT, dept VARCHAR(20), name VARCHAR(20), sal INT, d DATE);"


class TestTiebreakersNeverCreateFalseMatches(unittest.TestCase):
    """Two different queries must still give different results after tie-breaking.

    Each case runs both queries on a small SQLite table, before and after
    `_inject_deterministic_tiebreakers`, and checks that the injection never
    turns two different results into the same result.
    """

    def _rows(self, sql):
        """Runs `sql` on a fresh in-memory table and returns its rows in order."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(_SQLITE_ROWS)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def _assert_still_different(self, src, dest):
        """Fails if `src` and `dest` match after tie-breaking (or ever matched)."""
        self.assertNotEqual(sorted(self._rows(src)), sorted(self._rows(dest)))
        src_tb = _inject_deterministic_tiebreakers(src, schema_ddl=_EMP_DDL)
        dest_tb = _inject_deterministic_tiebreakers(dest, schema_ddl=_EMP_DDL)
        self.assertNotEqual(
            sorted(self._rows(src_tb)), sorted(self._rows(dest_tb)),
            msg=f"false match after injection:\n{src_tb}\n{dest_tb}",
        )

    def test_second_sort_key_is_kept_in_top_n(self):
        """`sal DESC, name ASC` vs `sal DESC, name DESC` with LIMIT 1 stay different."""
        self._assert_still_different(
            "SELECT dept, name, sal FROM emp ORDER BY sal DESC, name ASC LIMIT 1",
            "SELECT dept, name, sal FROM emp ORDER BY sal DESC, name DESC LIMIT 1",
        )

    def test_second_sort_key_is_kept_in_row_number(self):
        """A wrong second key inside ROW_NUMBER() still changes who is ranked 1."""
        self._assert_still_different(
            "SELECT * FROM (SELECT name, dept, ROW_NUMBER() OVER (PARTITION BY dept"
            " ORDER BY sal DESC, name ASC) AS rn FROM emp) t WHERE rn = 1",
            "SELECT * FROM (SELECT name, dept, ROW_NUMBER() OVER (PARTITION BY dept"
            " ORDER BY sal DESC, name DESC) AS rn FROM emp) t WHERE rn = 1",
        )

    def test_running_total_peers_are_not_split(self):
        """A running SUM over tied dates (peers) vs a ROWS frame stays different."""
        self._assert_still_different(
            "SELECT id, sal, SUM(sal) OVER (ORDER BY d) AS run FROM emp",
            "SELECT id, sal, SUM(sal) OVER (ORDER BY d ROWS BETWEEN UNBOUNDED"
            " PRECEDING AND CURRENT ROW) AS run FROM emp",
        )

    def test_running_count_with_extra_key_stays_different(self):
        """COUNT(*) OVER (ORDER BY d) vs (ORDER BY d, id) stays different."""
        self._assert_still_different(
            "SELECT id, COUNT(*) OVER (ORDER BY d) AS c FROM emp",
            "SELECT id, COUNT(*) OVER (ORDER BY d, id) AS c FROM emp",
        )

    def test_last_value_default_frame_stays_different(self):
        """LAST_VALUE with the default frame vs a ROWS frame stays different."""
        self._assert_still_different(
            "SELECT id, LAST_VALUE(name) OVER (PARTITION BY dept ORDER BY sal) AS lv FROM emp",
            "SELECT id, LAST_VALUE(name) OVER (PARTITION BY dept ORDER BY sal ROWS"
            " BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS lv FROM emp",
        )

    def test_nulls_last_idiom_second_key_is_kept(self):
        """`sal IS NULL, sal DESC` vs `sal IS NULL, sal ASC` with LIMIT stay different."""
        self._assert_still_different(
            "SELECT name, sal FROM emp ORDER BY sal IS NULL, sal DESC LIMIT 2",
            "SELECT name, sal FROM emp ORDER BY sal IS NULL, sal ASC LIMIT 2",
        )


class TestWhichWindowsGetTiebreakers(unittest.TestCase):
    """Tests for `_window_allows_tiebreakers` and the query's own keys staying intact."""

    def test_window_rules(self):
        """Order-only functions and ROWS frames get keys; peers and ranks do not."""
        self.assertTrue(_window_allows_tiebreakers("row_number", ""))
        self.assertTrue(_window_allows_tiebreakers("lag", ""))
        self.assertTrue(_window_allows_tiebreakers("sum", "ROWS BETWEEN 2 PRECEDING AND CURRENT ROW"))
        self.assertFalse(_window_allows_tiebreakers("sum", ""))
        self.assertFalse(_window_allows_tiebreakers("count", "RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"))
        self.assertFalse(_window_allows_tiebreakers("last_value", ""))
        self.assertFalse(_window_allows_tiebreakers("dense_rank", "ROWS UNBOUNDED PRECEDING"))

    def test_own_sort_keys_are_never_rewritten(self):
        """The query's own keys come through exactly as written, in both dialects."""
        sql = "SELECT name, sal FROM emp ORDER BY sal DESC, LOWER(name) ASC LIMIT 3"
        for dialect in ("mysql", "postgresql"):
            with self.subTest(dialect=dialect):
                out = _inject_deterministic_tiebreakers(
                    sql, dialect=dialect, schema_ddl=_EMP_DDL
                )
                self.assertIn("ORDER BY sal DESC, LOWER(name) ASC, ", out)

    def test_running_sum_without_frame_is_left_alone(self):
        """A plain running total is not changed at all."""
        sql = "SELECT id, SUM(sal) OVER (ORDER BY d) AS run FROM emp"
        self.assertEqual(
            _inject_deterministic_tiebreakers(sql, schema_ddl=_EMP_DDL), sql
        )


class TestTiebreakerRerunNeedsBothQueriesChanged(unittest.TestCase):
    """`_check_shared_window_tiebreaker` only re-runs when BOTH queries got tie-breakers."""

    def _orchestrator(self):
        """Builds an orchestrator whose query runners must never be called."""
        orch = QueryConversionOrchestrator(
            converter=MySQLToPGQueryConverter(),
            source_connection={},
            dest_connection={},
            source_schema=_EMP_DDL,
            dest_schema=_EMP_DDL,
        )
        orch._execute_source_query = mock.Mock(side_effect=AssertionError("re-ran"))  # pylint: disable=protected-access
        orch._execute_dest_query = mock.Mock(side_effect=AssertionError("re-ran"))  # pylint: disable=protected-access
        return orch

    def test_trailing_semicolon_is_not_a_change(self):
        """A query with nothing to tie-break plus a trailing ';' is not re-run."""
        ok, reason = self._orchestrator()._check_shared_window_tiebreaker(  # pylint: disable=protected-access
            "SELECT name FROM emp LIMIT 3;", "SELECT name FROM emp LIMIT 3;"
        )
        self.assertFalse(ok)
        self.assertIn("No deterministic tie-breakers", reason)

    def test_only_one_side_changed_is_not_re_run(self):
        """If only one of the two queries got tie-breakers, do not re-run."""
        ok, _ = self._orchestrator()._check_shared_window_tiebreaker(  # pylint: disable=protected-access
            "SELECT name, sal FROM emp ORDER BY sal DESC LIMIT 3",
            "SELECT name, sal FROM emp LIMIT 3",
        )
        self.assertFalse(ok)


class TestStrictTiesAndExactValues(unittest.TestCase):
    """Tests for the stricter tie, text and whole-number rules (Nov 9 review)."""

    def setUp(self):
        """Creates the converter under test."""
        self.converter = MySQLToPGQueryConverter()

    @staticmethod
    def _res(cols, rows):
        """Builds a query result from column names and rows."""
        return QueryResult(columns=cols, rows=rows, total_rows=len(rows), error=None)

    def test_case_sensitive_collate_keys_are_not_case_insensitive_ties(self):
        """Verify `ORDER BY name COLLATE utf8mb4_bin` keeps 'Apple' and 'apple' apart."""
        q = "SELECT name, n FROM t ORDER BY name COLLATE utf8mb4_bin"
        src = self._res(["name", "n"], [("Apple", 1), ("apple", 2)])
        dest = self._res(["name", "n"], [("apple", 2), ("Apple", 1)])
        self.assertFalse(self.converter.compare_results(src, dest, q)[0])
        # The default (case-insensitive) collation still treats them as tied.
        q_ci = "SELECT name, n FROM t ORDER BY name"
        self.assertTrue(self.converter.compare_results(src, dest, q_ci)[0])

    def test_trailing_space_is_not_a_tie(self):
        """Verify 'a' and 'a ' are different sort keys."""
        q = "SELECT name, n FROM t ORDER BY name"
        src = self._res(["name", "n"], [("a", 1), ("a ", 2)])
        dest = self._res(["name", "n"], [("a ", 2), ("a", 1)])
        self.assertFalse(self.converter.compare_results(src, dest, q)[0])

    def test_big_numeric_keys_are_not_merged_into_one_tie_group(self):
        """Verify large decimals and integers that differ are separate groups."""
        q = "SELECT name, total FROM t ORDER BY total"
        src = self._res(["name", "total"], [("x", decimal.Decimal("98765432.10")), ("y", decimal.Decimal("98765432.11"))])
        dest = self._res(["name", "total"], [("y", decimal.Decimal("98765432.11")), ("x", decimal.Decimal("98765432.10"))])
        self.assertFalse(self.converter.compare_results(src, dest, q)[0])
        q = "SELECT ts, who FROM t ORDER BY ts"
        src = self._res(["ts", "who"], [(1700000000000, "a"), (1700000000000, "b"), (1700000001000, "c")])
        dest = self._res(["ts", "who"], [(1700000000000, "b"), (1700000001000, "c"), (1700000000000, "a")])
        self.assertFalse(self.converter.compare_results(src, dest, q)[0])
        # Genuinely tied keys may still swap.
        dest_ok = self._res(["ts", "who"], [(1700000000000, "b"), (1700000000000, "a"), (1700000001000, "c")])
        self.assertTrue(self.converter.compare_results(src, dest_ok, q)[0])

    def test_text_cells_are_never_compared_as_numbers(self):
        """Verify '6.1' vs '6.14' and '1.10' vs '1.1' are mismatches."""
        self.assertFalse(self.converter._values_equal("6.1", "6.14"))
        self.assertFalse(self.converter._values_equal("1.10", "1.1"))
        self.assertFalse(self.converter._values_equal("0.1234567891", "0.1234567892"))
        # A MySQL decimal against a PostgreSQL float still uses the number rules.
        self.assertTrue(self.converter._values_equal(decimal.Decimal("11.3333"), 11.333333333))

    def test_whole_numbers_must_match_exactly(self):
        """Verify money totals, IDs and whole floats are compared exactly."""
        self.assertFalse(self.converter._values_equal(decimal.Decimal("98765432.10"), decimal.Decimal("98765432.11")))
        self.assertFalse(self.converter._values_equal(1000000000, decimal.Decimal("1000000000.5")))
        self.assertFalse(self.converter._values_equal(1234567.0, 1234568.0))
        self.assertTrue(self.converter._values_equal(1234567.0, 1234567))
        # A whole MySQL float against float noise on the PostgreSQL side is still fine.
        self.assertTrue(self.converter._values_equal(10.0, 10.000000000000002))

    def test_qualified_hidden_sort_key_does_not_match_a_same_named_column(self):
        """Verify `ORDER BY o.order_date` is not mapped to `DATE(o.order_date) AS order_date`."""
        q = "SELECT o.name, DATE(o.order_date) AS order_date FROM o ORDER BY o.order_date"
        src = self._res(["name", "order_date"], [("a", "2024-01-01"), ("b", "2024-01-01"), ("c", "2024-01-01")])
        dest = self._res(["name", "order_date"], [("c", "2024-01-01"), ("b", "2024-01-01"), ("a", "2024-01-01")])
        self.assertFalse(self.converter.compare_results(src, dest, q)[0])
        # With a `*` in the SELECT list the column can be an output column.
        q_star = "SELECT o.* FROM o ORDER BY o.order_date"
        src = self._res(["name", "order_date"], [("a", "2024-01-01"), ("b", "2024-01-01")])
        dest = self._res(["name", "order_date"], [("b", "2024-01-01"), ("a", "2024-01-01")])
        self.assertTrue(self.converter.compare_results(src, dest, q_star)[0])

    def test_alias_wins_over_expression_when_mapping_sort_keys(self):
        """Verify `SELECT a AS b, b AS a ... ORDER BY a` sorts by the second column."""
        q = "SELECT a AS b, b AS a FROM t2 ORDER BY a"
        src = self._res(["b", "a"], [(1, 10), (1, 20)])
        dest = self._res(["b", "a"], [(1, 20), (1, 10)])
        self.assertFalse(self.converter.compare_results(src, dest, q)[0])


class TestTiebreakerRerunGuards(unittest.TestCase):
    """Tests for the guards around the shared tie-breaker re-run."""

    def test_window_function_name_ignores_parens_inside_strings(self):
        """Verify a `(` inside a string literal does not change the function name."""
        from db_convertor.query_conversion_orchestrator import _window_function_name
        self.assertEqual(_window_function_name("SELECT name, SUM(CASE WHEN name = 'lag(' THEN 0 ELSE sal END) "), "sum")
        self.assertEqual(_window_function_name("SELECT a, ROW_NUMBER() "), "row_number")

    def test_dropped_sort_key_blocks_the_rerun(self):
        """Verify a converted query with fewer sort keys is refused before any re-run."""
        from db_convertor.query_conversion_orchestrator import _missing_sort_keys
        src = "SELECT name, sal FROM emp ORDER BY sal DESC, name LIMIT 1"
        self.assertTrue(_missing_sort_keys(src, 'SELECT name, sal FROM emp ORDER BY sal DESC LIMIT 1'))
        self.assertEqual(_missing_sort_keys(src, 'SELECT name, sal FROM emp ORDER BY sal DESC NULLS LAST, name LIMIT 1'), "")
        src_w = "SELECT d, ROW_NUMBER() OVER (PARTITION BY d ORDER BY s DESC, n) rn FROM t"
        self.assertTrue(_missing_sort_keys(src_w, 'SELECT d, ROW_NUMBER() OVER (PARTITION BY d ORDER BY s DESC) rn FROM t'))
        self.assertEqual(_missing_sort_keys(src_w, 'SELECT d, ROW_NUMBER() OVER (PARTITION BY d ORDER BY s DESC NULLS LAST, n) rn FROM t'), "")

    def test_fetch_first_and_comma_joins_get_tiebreakers(self):
        """Verify PostgreSQL `FETCH FIRST` counts as a top-N and `FROM a, b` yields both tables."""
        ddl = "CREATE TABLE a (id INT, x INT); CREATE TABLE b (id INT, y INT);"
        out = _inject_deterministic_tiebreakers("SELECT x FROM a ORDER BY x FETCH FIRST 3 ROWS ONLY", "postgresql", ddl)
        self.assertIn('"a"."id" ASC NULLS FIRST', out)
        out = _inject_deterministic_tiebreakers("SELECT a.x, b.y FROM a, b WHERE a.id = b.id ORDER BY a.x LIMIT 1", "mysql", ddl)
        self.assertIn("`b`.`y`", out)


class TestSameResultTieRule(unittest.TestCase):
    """Tests for how two sort keys from the SAME result are judged tied."""

    def test_computed_decimals_tie_but_stored_amounts_do_not(self):
        """Verify float-noise ties still count as ties while real differences do not."""
        same = MySQLToPGQueryConverter._same_number
        # MySQL doubles for two equal variances differ in the 15th digit; PostgreSQL numerics are equal.
        self.assertTrue(same(11.900826446280995, 11.900826446280991))
        self.assertTrue(same(decimal.Decimal("0.1234567890123460"), decimal.Decimal("0.1234567890123450")))
        # Real differences, however small the gap looks, stay apart.
        self.assertFalse(same(decimal.Decimal("98765432.10"), decimal.Decimal("98765432.11")))
        self.assertFalse(same(decimal.Decimal("0.5"), decimal.Decimal("0.49")))
        self.assertFalse(same(decimal.Decimal("0.49826989619377162630"), decimal.Decimal("0")))
        self.assertFalse(same(1700000000000, 1700000001000))
        self.assertTrue(same(1, decimal.Decimal("1.0")))


class TestCandidateVeto(unittest.TestCase):
    """Tests for the refusal of ctid sorting and hard-coded data values."""

    def setUp(self):
        """Creates the converter under test."""
        self.reject = MySQLToPGQueryConverter.reject_candidate
        self.src = "SELECT title, n FROM t WHERE kind = 'Lecture' AND d LIKE '2019%' ORDER BY n DESC LIMIT 3"

    def test_ctid_is_refused(self):
        """Verify sorting by ctid is rejected even when it is the only trick."""
        self.assertIn("ctid", self.reject(self.src, 'SELECT "title", n FROM t ORDER BY n DESC, ctid LIMIT 3'))
        # A literal containing the word ctid is not the column.
        self.assertEqual(self.reject(self.src, "SELECT title, n FROM t WHERE kind = 'Lecture' ORDER BY n DESC LIMIT 3"), "")

    def test_copied_data_values_are_refused(self):
        """Verify a CASE that names data values not present in the MySQL query is rejected."""
        bad = ("SELECT title, n FROM t WHERE kind = 'Lecture' ORDER BY n DESC, "
               "CASE WHEN title = 'Statistical Thermodynamics' THEN 0 ELSE 1 END LIMIT 3")
        self.assertIn("statistical thermodynamics", self.reject(self.src, bad))

    def test_harmless_literals_are_allowed(self):
        """Verify format strings, regexes, units, collations and literals from the MySQL query pass."""
        ok = ("SELECT TO_CHAR(d, 'YYYY-MM-DD'), TO_CHAR(n, 'FM999,990.00'), SPLIT_PART(title, ' ', 1), "
              "REGEXP_REPLACE(title, '[^0-9]', ''), d + INTERVAL '1 day', EXTRACT(EPOCH FROM d), "
              "COALESCE(kind, 'Lecture') FROM t WHERE d::text LIKE '2019%' AND title ~ '^\\d+' "
              "ORDER BY title COLLATE \"und-x-icu\" ASC NULLS FIRST, CASE WHEN GROUPING(title) = 1 THEN 'zzz' ELSE title END LIMIT 3")
        self.assertEqual(self.reject(self.src, ok), "")
        # A number MySQL wrote bare and PostgreSQL quotes is fine.
        self.assertEqual(self.reject("SELECT a FROM t WHERE year = 2022", "SELECT a FROM t WHERE year = '2022'"), "")

    def test_orchestrator_veto_blocks_mechanical_match_and_rerun(self):
        """Verify a vetoed candidate never matches, even with identical results."""
        orch = QueryConversionOrchestrator(
            converter=MySQLToPGQueryConverter(), source_connection={}, dest_connection={},
            source_schema="", dest_schema="", max_attempts=1)
        res = QueryResult(columns=["a"], rows=[(1,)], total_rows=1, error=None)
        ok, why = orch._compare(res, res, "SELECT a FROM t ORDER BY a", "SELECT a FROM t ORDER BY a, ctid")
        self.assertFalse(ok); self.assertIn("ctid", why)
        ok, why = orch._check_shared_window_tiebreaker("SELECT a FROM t ORDER BY a LIMIT 1", "SELECT a FROM t ORDER BY a, ctid LIMIT 1")
        self.assertFalse(ok); self.assertIn("ctid", why)
        # Existing converters have no veto and are unaffected.
        plain = QueryConversionOrchestrator(
            converter=SQLiteToPGQueryConverter(), source_connection={}, dest_connection={},
            source_schema="", dest_schema="", max_attempts=1)
        self.assertEqual(plain._veto("SELECT a FROM t", "SELECT a FROM t ORDER BY ctid"), "")

    def test_prompt_tells_the_model_about_the_veto(self):
        """Verify the next prompt names the rejected trick."""
        prompt = MySQLToPGQueryConverter().get_conversion_prompt(
            "s", "d", self.src, converted_query="SELECT title, n FROM t ORDER BY n DESC, ctid LIMIT 3")
        self.assertIn("REJECTED", prompt); self.assertIn("ctid", prompt)


    def test_float_noise_cluster_on_primary_key_stays_one_tie_group(self):
        """Verify 1-ULP float differences on key 1 do not split on key 2."""
        c = MySQLToPGQueryConverter()
        q = "SELECT id, cov FROM t ORDER BY cov DESC, id ASC"
        # Both rows represent sqrt(2); 1-ULP float difference swaps which row
        # gets .0951 vs .0950 between MySQL and PostgreSQL.
        src = QueryResult(
            columns=["id", "cov"],
            rows=[("a", 1.4142135623730951), ("b", 1.4142135623730950)],
            total_rows=2,
            error=None,
        )
        dest = QueryResult(
            columns=["id", "cov"],
            rows=[("b", 1.4142135623730951), ("a", 1.4142135623730950)],
            total_rows=2,
            error=None,
        )
        self.assertTrue(c.compare_results(src, dest, q)[0])
        # When key 1 is bit-identical (e.g. 0.0 == 0.0), key 2 is still enforced.
        src_exact = QueryResult(
            columns=["id", "cov"],
            rows=[("a", 0.0), ("b", 0.0)],
            total_rows=2,
            error=None,
        )
        dest_reversed = QueryResult(
            columns=["id", "cov"],
            rows=[("b", 0.0), ("a", 0.0)],
            total_rows=2,
            error=None,
        )
        self.assertFalse(c.compare_results(src_exact, dest_reversed, q)[0])

    def test_second_sort_key_is_checked_when_databases_print_key_one_differently(self):
        """Verify MySQL `11.3333` vs PostgreSQL `11.3333333333333333` is not rounding error."""
        c = MySQLToPGQueryConverter()
        q = "SELECT name, AVG(x) AS a FROM t GROUP BY name ORDER BY a DESC, name"
        src = QueryResult(
            columns=["name", "a"],
            rows=[("amy", decimal.Decimal("11.3333")), ("bob", decimal.Decimal("11.3333")), ("cat", decimal.Decimal("11.3333"))],
            total_rows=3,
            error=None,
        )
        long_avg = decimal.Decimal("11.3333333333333333")
        # Names in the right order: accepted.
        same_order = QueryResult(
            columns=["name", "a"],
            rows=[("amy", long_avg), ("bob", long_avg), ("cat", long_avg)],
            total_rows=3,
            error=None,
        )
        self.assertTrue(c.compare_results(src, same_order, q)[0])
        # Names backwards: the three rows are exactly tied on the average in
        # each result, so the second sort key must still be enforced.
        backwards = QueryResult(
            columns=["name", "a"],
            rows=[("cat", long_avg), ("bob", long_avg), ("amy", long_avg)],
            total_rows=3,
            error=None,
        )
        self.assertFalse(c.compare_results(src, backwards, q)[0])
        # Same when MySQL returns a float and PostgreSQL an exact decimal.
        src_f = QueryResult(columns=["name", "a"], rows=[("amy", 0.1), ("bob", 0.1)], total_rows=2, error=None)
        dest_d = QueryResult(columns=["name", "a"], rows=[("bob", decimal.Decimal("0.1")), ("amy", decimal.Decimal("0.1"))], total_rows=2, error=None)
        self.assertFalse(c.compare_results(src_f, dest_d, q)[0])

    def test_quoted_ctid_is_refused(self):
        """Verify `"ctid"` in double quotes is refused like bare ctid."""
        reject = MySQLToPGQueryConverter.reject_candidate
        self.assertIn("ctid", reject("SELECT a FROM t ORDER BY a", 'SELECT "a" FROM "t" ORDER BY "a", "t"."ctid"'))
        # The word inside a text value is not a use of the column.
        self.assertEqual(reject("SELECT a FROM t WHERE n = 'ctid'", "SELECT \"a\" FROM \"t\" WHERE \"n\" = 'ctid'"), "")

    def test_copied_numbers_in_a_sort_are_refused(self):
        """Verify a CASE on a copied ID inside ORDER BY is refused, and ordinary numbers are not."""
        reject = MySQLToPGQueryConverter.reject_candidate
        src = "SELECT name, sal FROM emp ORDER BY sal DESC LIMIT 10"
        # Final ORDER BY, window ORDER BY, IN (...) and wrapped in a function.
        for bad in (
            'SELECT "name", "sal" FROM "emp" ORDER BY "sal" DESC, CASE WHEN "id" = 48213 THEN 0 ELSE 1 END LIMIT 10',
            'SELECT "name", ROW_NUMBER() OVER (ORDER BY "sal" DESC, CASE WHEN "e"."id" = 48213 THEN 0 ELSE 1 END) FROM "emp" "e"',
            'SELECT "name", "sal" FROM "emp" ORDER BY "sal" DESC, CASE WHEN "id" IN (48213, 7) THEN 0 ELSE 1 END LIMIT 10',
            'SELECT "name", "sal" FROM "emp" ORDER BY "sal" DESC, COALESCE(CASE WHEN id = 48213 THEN 0 END, 1) LIMIT 10',
        ):
            self.assertIn("48213", reject(src, bad), bad)
        # Numbers an honest translation adds are fine: rounding, casts, LIMIT,
        # a CASE outside any ORDER BY, and the flags 0 and 1.
        for ok in (
            'SELECT "name", ROUND("sal", 4) FROM "emp" ORDER BY "sal" DESC LIMIT 10',
            'SELECT "name", CASE WHEN "grade" = 3 THEN \'x\' ELSE \'y\' END FROM "emp" ORDER BY "sal" DESC LIMIT 10',
            'SELECT "name", "sal"::NUMERIC(12, 2) FROM "emp" WHERE "dept" = 48213 ORDER BY "sal" DESC LIMIT 10',
            'SELECT "name", "sal" FROM "emp" ORDER BY CASE WHEN "active" = 1 THEN 0 ELSE 1 END, "sal" DESC LIMIT 10',
            'SELECT "name", (SELECT CASE WHEN "id" = 48213 THEN 1 END FROM "x") FROM "emp" ORDER BY "sal" DESC LIMIT 10',
        ):
            self.assertEqual(reject(src, ok), "", ok)
        # A number the MySQL query itself sorts on is a translation, not a copy.
        self.assertEqual(
            reject(
                "SELECT name FROM emp ORDER BY FIELD(grade, 3, 5)",
                'SELECT "name" FROM "emp" ORDER BY CASE WHEN "grade" = 3 THEN 1 WHEN "grade" = 5 THEN 2 ELSE 0 END',
            ),
            "",
        )


class TestReviewFollowups(unittest.TestCase):
    """Tests for model-judgment tagging, trailing-zero Decimals, and `SELECT a.*` sort keys."""

    def test_model_judgment_matches_are_tagged(self):
        """Verify matches declared by the AI model carry `[model_judgment]` in `reason`."""
        orch = QueryConversionOrchestrator(
            converter=MySQLToPGQueryConverter(),
            source_connection={},
            dest_connection={},
            source_schema="",
            dest_schema="",
            max_attempts=2,
        )
        src_res = QueryResult(columns=["a"], rows=[(1,)], total_rows=1, error=None)
        orch._execute_source_query = mock.Mock(return_value=src_res)  # pylint: disable=protected-access

        # AI declares result_matched with notes: reason gets the [model_judgment] prefix.
        with mock.patch(
            "db_convertor.query_conversion_orchestrator.gemini_inference",
            return_value=json.dumps({
                "conversion_finished": "result_matched",
                "notes": "Minor float rounding in STDDEV",
            }),
        ):
            res = orch.convert_query("SELECT a FROM t", question_id="q1", verbose=False)
        self.assertEqual(res.status, ConversionStatus.RESULT_MATCHED)
        self.assertEqual(res.reason, "[model_judgment] Minor float rounding in STDDEV")

        # AI declares unable_to_match: reason keeps plain notes with no match tag.
        with mock.patch(
            "db_convertor.query_conversion_orchestrator.gemini_inference",
            return_value=json.dumps({
                "conversion_finished": "unable_to_match",
                "notes": "Source query uses session variables",
            }),
        ):
            unable = orch.convert_query("SELECT a FROM t", question_id="q2", verbose=False)
        self.assertEqual(unable.status, ConversionStatus.UNABLE_TO_MATCH)
        self.assertEqual(unable.reason, "Source query uses session variables")

    def test_whole_decimal_with_trailing_zeros_matches_integer(self):
        """Verify `5` matches `Decimal('5.00')` while keeping 4-decimal rounding intact."""
        c = MySQLToPGQueryConverter()
        # Whole Decimal with trailing zeros matches an integer in both directions.
        self.assertTrue(c._values_equal(5, decimal.Decimal("5.00")))  # pylint: disable=protected-access
        self.assertTrue(c._values_equal(decimal.Decimal("5.00"), 5))  # pylint: disable=protected-access
        self.assertFalse(c._values_equal(5, decimal.Decimal("5.50")))  # pylint: disable=protected-access
        # A 4-decimal MySQL average that rounded to .0000 still matches the unrounded PG value.
        self.assertTrue(
            c._values_equal(decimal.Decimal("5.0000"), decimal.Decimal("5.00001"))  # pylint: disable=protected-access
        )
        self.assertFalse(
            c._values_equal(decimal.Decimal("5.0000"), decimal.Decimal("5.0001"))  # pylint: disable=protected-access
        )

    def test_star_plus_extra_columns_maps_sort_key_to_real_column(self):
        """Verify `SELECT a.*, expr AS total ORDER BY total` checks `total`, not column 2."""
        c = MySQLToPGQueryConverter()
        q = "SELECT a.*, price * qty AS total FROM orders a ORDER BY total DESC"
        cols = ["id", "price", "qty", "total"]
        # Both rows tie on `total` (20) even though `price` (column 2) differs: swapping is valid.
        src = QueryResult(
            columns=cols, rows=[(1, 10, 2, 20), (2, 4, 5, 20)], total_rows=2, error=None
        )
        dest_tied_swap = QueryResult(
            columns=cols, rows=[(2, 4, 5, 20), (1, 10, 2, 20)], total_rows=2, error=None
        )
        self.assertTrue(c.compare_results(src, dest_tied_swap, q)[0])

        # Rows with different `total` values (30 vs 20) must NOT swap even if `price` ties (10 == 10).
        src_diff = QueryResult(
            columns=cols, rows=[(1, 10, 3, 30), (2, 10, 2, 20)], total_rows=2, error=None
        )
        dest_wrong_order = QueryResult(
            columns=cols, rows=[(2, 10, 2, 20), (1, 10, 3, 30)], total_rows=2, error=None
        )
        self.assertFalse(c.compare_results(src_diff, dest_wrong_order, q)[0])

        # Also works when an alias sits between two stars (`SELECT a.*, expr AS total, b.*`).
        q_two_stars = "SELECT a.*, price * qty AS total, b.* FROM orders a JOIN meta b ON a.id = b.id ORDER BY total DESC"
        cols_two = ["id", "price", "qty", "total", "note"]
        src_two = QueryResult(
            columns=cols_two,
            rows=[(1, 10, 2, 20, "x"), (2, 4, 5, 20, "y")],
            total_rows=2,
            error=None,
        )
        dest_two = QueryResult(
            columns=cols_two,
            rows=[(2, 4, 5, 20, "y"), (1, 10, 2, 20, "x")],
            total_rows=2,
            error=None,
        )
        self.assertTrue(c.compare_results(src_two, dest_two, q_two_stars)[0])


if __name__ == "__main__":
    unittest.main()
