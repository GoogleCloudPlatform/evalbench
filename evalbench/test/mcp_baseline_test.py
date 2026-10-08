"""Unit tests for the readability baseline stores.

The behaviour that matters most is picking the *newest daily* run for an
endpoint: an older one would resurrect stale findings, and an ad-hoc one would
make the next scheduled run compare against a surface nobody reviewed.
"""

import csv
import os
import tempfile
import unittest

from evaluator.mcp_readability import baseline as baseline_mod
from evaluator.mcp_readability import orchestrator as orchestrator_mod
from evaluator.mcp_readability.baseline import (
    BigQueryBaselineStore,
    LocalResultsBaselineStore,
    NullBaselineStore,
    build_store,
)


_KEY = "AlloyDB|http://x|PROD"

_COLUMNS = [
    "mcp_readability_product_name",
    "mcp_readability_source_url",
    "mcp_readability_endpoint_type",
    "mcp_readability_check_timestamp",
    "mcp_readability_check_timestamp_utc",
    "mcp_readability_judge_fingerprint",
    "mcp_readability_judge_components_json",
    "mcp_readability_tool_fingerprints_json",
    "mcp_readability_llm_feedback_json",
    "mcp_readability_score",
    "job_id",
]


def _write_run(root, job_id, rows):
    directory = os.path.join(root, job_id)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "evals.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in _COLUMNS})
    return path


def _row(key, timestamp, job_id, score="70", feedback='{"summary": "s"}'):
    product, source_url, endpoint_type = (key.split("|") + ["", ""])[:3]
    return {
        "mcp_readability_product_name": product,
        "mcp_readability_source_url": source_url,
        "mcp_readability_endpoint_type": endpoint_type,
        "mcp_readability_check_timestamp": timestamp,
        "mcp_readability_check_timestamp_utc": timestamp,
        "mcp_readability_judge_fingerprint": "jfp",
        "mcp_readability_judge_components_json": '{"judge_model": "m"}',
        "mcp_readability_tool_fingerprints_json": '{"a": "fp"}',
        "mcp_readability_llm_feedback_json": feedback,
        "mcp_readability_score": score,
        "job_id": job_id,
    }


class NullStoreTest(unittest.TestCase):

    def test_never_finds_a_baseline(self):
        store = NullBaselineStore()
        store.prime([_KEY])
        self.assertIsNone(store.load(_KEY))


class LocalResultsStoreTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_picks_the_newest_run(self):
        _write_run(
            self.root, "job-old",
            [_row(_KEY, "2026-09-01T00:00:00Z", "job-old", score="10")],
        )
        _write_run(
            self.root, "job-new",
            [_row(_KEY, "2026-09-09T00:00:00Z", "job-new", score="90")],
        )
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime([_KEY])
        found = store.load(_KEY)
        self.assertEqual(found.job_id, "job-new")
        self.assertEqual(found.readability_score, 90)

    def test_parses_the_json_columns(self):
        _write_run(self.root, "job", [_row(_KEY, "2026-09-09T00:00:00Z", "job")])
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime([_KEY])
        found = store.load(_KEY)
        self.assertEqual(found.tool_fingerprints, {"a": "fp"})
        self.assertEqual(found.judge_components, {"judge_model": "m"})
        self.assertEqual(found.judge_fingerprint, "jfp")
        self.assertEqual(found.feedback, {"summary": "s"})

    def test_utc_timestamp_wins_over_the_naive_column(self):
        """Both are written today; only one is comparable across hosts."""
        row = _row(_KEY, "2026-09-09T00:00:00", "job")
        row["mcp_readability_check_timestamp_utc"] = "2026-09-09T07:00:00+00:00"
        _write_run(self.root, "job", [row])
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime([_KEY])
        self.assertEqual(
            store.load(_KEY).check_timestamp, "2026-09-09T07:00:00+00:00"
        )

    def test_rows_predating_the_utc_column_still_load(self):
        row = _row(_KEY, "2026-09-09T00:00:00", "job")
        row["mcp_readability_check_timestamp_utc"] = ""
        _write_run(self.root, "job", [row])
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime([_KEY])
        self.assertEqual(
            store.load(_KEY).check_timestamp, "2026-09-09T00:00:00"
        )

    def test_only_requested_endpoints_are_kept(self):
        _write_run(
            self.root, "job",
            [
                _row("Wanted|http://w|PROD", "2026-09-09T00:00:00Z", "job"),
                _row("Other|http://o|PROD", "2026-09-09T00:00:00Z", "job"),
            ],
        )
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime(["Wanted|http://w|PROD"])
        self.assertIsNotNone(store.load("Wanted|http://w|PROD"))
        self.assertIsNone(store.load("Other|http://o|PROD"))

    def test_rows_without_any_identity_are_ignored(self):
        _write_run(self.root, "job", [_row("||", "2026-09-09T00:00:00Z", "job")])
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime([_KEY])
        self.assertIsNone(store.load(_KEY))

    def test_missing_results_directory_is_not_an_error(self):
        store = LocalResultsBaselineStore(results_dir=os.path.join(self.root, "x"))
        store.prime([_KEY])
        self.assertIsNone(store.load(_KEY))

    def test_unreadable_run_is_skipped_not_raised(self):
        # An evals.csv that cannot be opened (here: it is a directory) must cost
        # a re-judge, not abort the job.
        os.makedirs(os.path.join(self.root, "bad", "evals.csv"))
        _write_run(
            self.root, "good", [_row(_KEY, "2026-09-09T00:00:00Z", "good")]
        )
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime([_KEY])  # must not raise
        self.assertEqual(store.load(_KEY).job_id, "good")

    def test_scan_is_capped(self):
        for i in range(5):
            _write_run(
                self.root, f"job-{i}",
                [_row(f"P{i}|http://x|PROD", "2026-09-09T00:00:00Z", f"job-{i}")],
            )
        store = LocalResultsBaselineStore(results_dir=self.root, max_runs=1)
        store.prime([f"P{i}|http://x|PROD" for i in range(5)])
        found = [i for i in range(5) if store.load(f"P{i}|http://x|PROD") is not None]
        self.assertEqual(len(found), 1)


class BuildStoreTest(unittest.TestCase):

    def test_absent_block_is_the_null_store(self):
        self.assertIsInstance(build_store({}), NullBaselineStore)
        self.assertIsInstance(build_store(None), NullBaselineStore)

    def test_explicit_none_is_the_null_store(self):
        self.assertIsInstance(build_store({"store": "none"}), NullBaselineStore)

    def test_local_and_bigquery(self):
        self.assertIsInstance(
            build_store({"store": "local"}), LocalResultsBaselineStore
        )
        self.assertIsInstance(
            build_store({"store": "bigquery"}), BigQueryBaselineStore
        )

    def test_unknown_store_fails_fast(self):
        with self.assertRaises(ValueError):
            build_store({"store": "redis"})


class BigQueryStoreTest(unittest.TestCase):

    def test_prime_with_no_keys_does_not_touch_bigquery(self):
        # No import of google.cloud.bigquery, no client, no query.
        store = BigQueryBaselineStore()
        store.prime([])
        self.assertIsNone(store.load(_KEY))

    def test_rows_are_absorbed_like_csv_rows(self):
        store = BigQueryBaselineStore()
        store._absorb(_row(_KEY, "2026-09-09T00:00:00Z", "job"), {_KEY})
        self.assertEqual(store.load(_KEY).job_id, "job")

    def test_sql_key_matches_the_python_key(self):
        """The two definitions of "same endpoint" must stay identical.

        A drift would partition on one identity and look up another, so every
        endpoint would silently miss its baseline.
        """
        row = _row(_KEY, "2026-09-09T00:00:00Z", "job")
        self.assertEqual(
            baseline_mod._row_key(row), _KEY
        )
        # Every column is trimmed and coalesced, matching _row_key. Without the
        # COALESCE a single NULL column would make the whole concatenation NULL
        # and that endpoint would silently never match its own baseline.
        for column in baseline_mod._KEY_COLUMNS:
            self.assertIn(f"COALESCE(TRIM({column}), '')",
                          baseline_mod._KEY_SQL)
        # The quoted literal, not a bare "|": SQL's concat operator is "||".
        self.assertEqual(
            baseline_mod._KEY_SQL.count(f"'{baseline_mod._KEY_SEPARATOR}'"),
            len(baseline_mod._KEY_COLUMNS) - 1,
        )

    def test_key_columns_ignore_surrounding_whitespace(self):
        row = _row(_KEY, "2026-09-09T00:00:00Z", "job")
        row["mcp_readability_product_name"] = "  AlloyDB  "
        self.assertEqual(baseline_mod._row_key(row), _KEY)

    def test_daily_tag_matches_the_orchestrator(self):
        """The tag is duplicated to avoid an import cycle, so pin the two.

        A drift here is invisible: the query would match no rows and every
        endpoint would look like a first run.
        """
        self.assertEqual(
            baseline_mod._DAILY_RUN_TAG, orchestrator_mod.DAILY_RUN_TAG
        )
        self.assertIn(baseline_mod._DAILY_RUN_TAG, orchestrator_mod.RUN_TAGS)


if __name__ == "__main__":
    unittest.main()
