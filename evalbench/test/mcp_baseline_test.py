"""Unit tests for the readability baseline stores.

Two behaviours matter most: picking the *newest* previous run for an endpoint
(picking an older one would resurrect stale findings), and degrading to "no
baseline" on any read failure rather than aborting the job, since a baseline is
an optimisation and not a measurement.
"""

import csv
import datetime
import os
import tempfile
import unittest

from evaluator.mcp_readability import baseline as baseline_mod
from evaluator.mcp_readability import orchestrator as orchestrator_mod
from evaluator.mcp_readability.orchestrator import McpReadabilityOrchestrator
from evaluator.mcp_readability.baseline import (
    BigQueryBaselineStore,
    LocalResultsBaselineStore,
    NullBaselineStore,
    build_store,
    is_expired,
)
from scorers import mcp_carry_forward as cf
from scorers.mcp_carry_forward import Baseline


_COLUMNS = [
    "mcp_readability_endpoint_key",
    "mcp_readability_check_timestamp_utc",
    "mcp_readability_check_timestamp",
    "mcp_readability_judge_fingerprint",
    "mcp_readability_judge_components_json",
    "mcp_readability_tool_fingerprints_json",
    "mcp_readability_llm_feedback_json",
    "mcp_readability_feedback_provenance_json",
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
    return {
        "mcp_readability_endpoint_key": key,
        "mcp_readability_check_timestamp_utc": timestamp,
        "mcp_readability_judge_fingerprint": "jf",
        "mcp_readability_judge_components_json": '{"judge_model": "m"}',
        "mcp_readability_tool_fingerprints_json": '{"a": "fp"}',
        "mcp_readability_llm_feedback_json": feedback,
        "mcp_readability_feedback_provenance_json": '{"baseline_origin_job_id": "j0"}',
        "mcp_readability_score": score,
        "job_id": job_id,
    }


class NullStoreTest(unittest.TestCase):

    def test_never_finds_a_baseline(self):
        store = NullBaselineStore()
        store.prime(["key"])
        self.assertIsNone(store.load("key"))


class LocalResultsStoreTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_picks_the_newest_run(self):
        _write_run(
            self.root, "job-old",
            [_row("key", "2026-09-01T00:00:00Z", "job-old", score="10")],
        )
        _write_run(
            self.root, "job-new",
            [_row("key", "2026-09-09T00:00:00Z", "job-new", score="90")],
        )
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime(["key"])
        found = store.load("key")
        self.assertEqual(found.job_id, "job-new")
        self.assertEqual(found.readability_score, 90)

    def test_parses_the_json_columns(self):
        _write_run(self.root, "job", [_row("key", "2026-09-09T00:00:00Z", "job")])
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime(["key"])
        found = store.load("key")
        self.assertEqual(found.tool_fingerprints, {"a": "fp"})
        self.assertEqual(found.judge_components, {"judge_model": "m"})
        self.assertEqual(found.feedback, {"summary": "s"})
        self.assertEqual(found.provenance["baseline_origin_job_id"], "j0")

    def test_only_requested_endpoints_are_kept(self):
        _write_run(
            self.root, "job",
            [
                _row("wanted", "2026-09-09T00:00:00Z", "job"),
                _row("other", "2026-09-09T00:00:00Z", "job"),
            ],
        )
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime(["wanted"])
        self.assertIsNotNone(store.load("wanted"))
        self.assertIsNone(store.load("other"))

    def test_falls_back_to_the_naive_timestamp_column(self):
        row = _row("key", "", "job")
        row["mcp_readability_check_timestamp"] = "2026-09-09T12:00:00"
        _write_run(self.root, "job", [row])
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime(["key"])
        self.assertEqual(store.load("key").check_timestamp, "2026-09-09T12:00:00")

    def test_rows_without_an_endpoint_key_are_ignored(self):
        _write_run(self.root, "job", [_row("", "2026-09-09T00:00:00Z", "job")])
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime(["key"])
        self.assertIsNone(store.load("key"))

    def test_missing_results_directory_is_not_an_error(self):
        store = LocalResultsBaselineStore(results_dir=os.path.join(self.root, "x"))
        store.prime(["key"])
        self.assertIsNone(store.load("key"))

    def test_unreadable_run_is_skipped_not_raised(self):
        # An evals.csv that cannot be opened (here: it is a directory) must cost
        # a re-judge, not abort the job.
        os.makedirs(os.path.join(self.root, "bad", "evals.csv"))
        _write_run(
            self.root, "good", [_row("key", "2026-09-09T00:00:00Z", "good")]
        )
        store = LocalResultsBaselineStore(results_dir=self.root)
        store.prime(["key"])  # must not raise
        self.assertEqual(store.load("key").job_id, "good")

    def test_scan_is_capped(self):
        for i in range(5):
            _write_run(
                self.root, f"job-{i}",
                [_row(f"key-{i}", "2026-09-09T00:00:00Z", f"job-{i}")],
            )
        store = LocalResultsBaselineStore(results_dir=self.root, max_runs=1)
        store.prime([f"key-{i}" for i in range(5)])
        found = [i for i in range(5) if store.load(f"key-{i}") is not None]
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


class ExpiryTest(unittest.TestCase):

    def _baseline(self, days_ago, key="00000000"):
        stamp = datetime.datetime(
            2026, 9, 14, tzinfo=datetime.timezone.utc
        ) - datetime.timedelta(days=days_ago)
        return Baseline(endpoint_key=key, check_timestamp=stamp.isoformat())

    def _now(self):
        return datetime.datetime(2026, 9, 14, tzinfo=datetime.timezone.utc)

    def test_fresh_baseline_is_not_expired(self):
        self.assertFalse(is_expired(self._baseline(1), 90, now=self._now()))

    def test_old_baseline_is_expired(self):
        self.assertTrue(is_expired(self._baseline(200), 90, now=self._now()))

    def test_zero_max_age_disables_expiry(self):
        self.assertFalse(is_expired(self._baseline(9999), 0, now=self._now()))

    def test_unparseable_timestamp_expires(self):
        stale = Baseline(endpoint_key="k", check_timestamp="not a date")
        self.assertTrue(is_expired(stale, 90, now=self._now()))

    def test_expiry_is_staggered_across_endpoints(self):
        # Same age, different endpoints: they must not all flip on one day, or
        # a whole fleet re-judges at once and every number moves together.
        verdicts = {
            is_expired(self._baseline(95, key=f"endpoint-{i}"), 90,
                       now=self._now())
            for i in range(200)
        }
        self.assertEqual(verdicts, {True, False})

    def test_stagger_stays_inside_the_window(self):
        for key in ("", "endpoint-1", "a" * 100, "alloydb-prod"):
            with self.subTest(key=key):
                self.assertLess(baseline_mod._stagger(key, 90), 90)

    def test_a_very_old_baseline_expires_whatever_the_stagger(self):
        for i in range(20):
            with self.subTest(i=i):
                self.assertTrue(
                    is_expired(
                        self._baseline(1000, key=f"endpoint-{i}"), 90,
                        now=self._now(),
                    )
                )

    def test_naive_timestamps_are_treated_as_utc(self):
        stale = Baseline(endpoint_key="00000000",
                         check_timestamp="2020-01-01T00:00:00")
        self.assertTrue(is_expired(stale, 90, now=self._now()))


class BigQueryStoreTest(unittest.TestCase):

    def test_prime_with_no_keys_does_not_touch_bigquery(self):
        # No import of google.cloud.bigquery, no client, no query.
        store = BigQueryBaselineStore()
        store.prime([])
        self.assertIsNone(store.load("key"))

    def test_rows_are_absorbed_like_csv_rows(self):
        store = BigQueryBaselineStore()
        store._absorb(_row("key", "2026-09-09T00:00:00Z", "job"), {"key"})
        self.assertEqual(store.load("key").job_id, "job")


class _ExplodingStore:
    def prime(self, endpoint_keys):
        raise RuntimeError("no read permission on the dataset")

    def load(self, endpoint_key):
        raise RuntimeError("no read permission on the dataset")


def _orchestrator(store, **attrs):
    """A bare orchestrator with only the carry-forward state populated."""
    orchestrator = McpReadabilityOrchestrator.__new__(McpReadabilityOrchestrator)
    orchestrator.job_id = "job-under-test"
    orchestrator.baseline_store = store
    orchestrator.baseline_max_age_days = 90
    orchestrator.force_refresh = False
    orchestrator.force_refresh_products = set()
    orchestrator.baseline_unavailable = False
    for key, value in attrs.items():
        setattr(orchestrator, key, value)
    return orchestrator


class StoreFailureDegradesTest(unittest.TestCase):
    """A baseline is an optimisation, so a read failure must never abort a run."""

    def test_prime_failure_is_swallowed_and_recorded(self):
        orchestrator = _orchestrator(_ExplodingStore())
        endpoint = {
            "product_name": "AlloyDB",
            "endpoint_type": "PROD",
            "tools_source": {"url": "http://x"},
        }
        orchestrator._prime_baselines([endpoint])  # must not raise
        self.assertTrue(orchestrator.baseline_unavailable)
        context = orchestrator._baseline_context(endpoint, "key", {"a": "fp"})
        self.assertEqual(context.override_reason, cf.BASELINE_UNAVAILABLE)
        self.assertIsNone(context.baseline)

    def test_load_failure_is_swallowed(self):
        orchestrator = _orchestrator(_ExplodingStore())
        context = orchestrator._baseline_context({}, "key", {"a": "fp"})
        self.assertEqual(context.override_reason, cf.BASELINE_UNAVAILABLE)

    def test_expired_baseline_is_an_announced_rejudge(self):
        class _Store:
            def prime(self, keys):
                pass

            def load(self, key):
                return Baseline(
                    endpoint_key=key, check_timestamp="2020-01-01T00:00:00Z"
                )

        context = _orchestrator(_Store())._baseline_context({}, "k", {})
        self.assertEqual(context.override_reason, cf.BASELINE_EXPIRED)
        # Still attached: the override stops its findings being carried, but it
        # remains the reference for "what changed since the previous review".
        self.assertIsNotNone(context.baseline)
        self.assertEqual(
            cf.decide(context, "any-fingerprint").mode, cf.MODE_FULL_JUDGE
        )

    def test_force_refresh_still_keeps_the_baseline_for_comparison(self):
        class _Store:
            def prime(self, keys):
                pass

            def load(self, key):
                return Baseline(
                    endpoint_key=key, check_timestamp="2026-09-09T00:00:00Z"
                )

        orchestrator = _orchestrator(_Store(), force_refresh=True)
        context = orchestrator._baseline_context({}, "k", {})
        self.assertEqual(context.override_reason, cf.FORCED_REFRESH)
        self.assertIsNotNone(context.baseline)
        self.assertEqual(
            cf.decide(context, "any-fingerprint").mode, cf.MODE_FULL_JUDGE
        )

    def test_force_refresh_skips_the_baseline(self):
        orchestrator = _orchestrator(NullBaselineStore(), force_refresh=True)
        context = orchestrator._baseline_context({}, "k", {})
        self.assertEqual(context.override_reason, cf.FORCED_REFRESH)

    def test_force_refresh_products_is_per_product(self):
        orchestrator = _orchestrator(
            NullBaselineStore(), force_refresh_products={"alloydb"}
        )
        forced = orchestrator._baseline_context(
            {"product_name": "AlloyDB"}, "k", {}
        )
        other = orchestrator._baseline_context(
            {"product_name": "Cloud SQL"}, "k", {}
        )
        self.assertEqual(forced.override_reason, cf.FORCED_REFRESH)
        self.assertEqual(other.override_reason, "")

    def test_null_store_yields_no_override_and_no_baseline(self):
        context = _orchestrator(NullBaselineStore())._baseline_context(
            {}, "k", {"a": "fp"}
        )
        self.assertEqual(context.override_reason, "")
        self.assertIsNone(context.baseline)


class EndpointKeyTest(unittest.TestCase):

    def test_explicit_id_wins(self):
        self.assertEqual(
            orchestrator_mod._endpoint_key(
                {"id": "alloydb-prod"}, "AlloyDB", "http://x", "PROD"
            ),
            "alloydb-prod",
        )

    def test_derived_key_is_stable(self):
        args = ({}, "AlloyDB", "http://x", "PROD")
        self.assertEqual(
            orchestrator_mod._endpoint_key(*args),
            orchestrator_mod._endpoint_key(*args),
        )

    def test_each_identity_field_changes_the_key(self):
        base = orchestrator_mod._endpoint_key(
            {}, "AlloyDB", "http://x", "PROD"
        )
        variants = [
            ({}, "Cloud SQL", "http://x", "PROD"),
            ({}, "AlloyDB", "http://y", "PROD"),
            ({}, "AlloyDB", "http://x", "STAGING"),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(
                    base, orchestrator_mod._endpoint_key(*variant)
                )

    def test_explicit_id_survives_a_product_rename(self):
        self.assertEqual(
            orchestrator_mod._endpoint_key(
                {"id": "pinned"}, "AlloyDB", "http://x", "PROD"
            ),
            orchestrator_mod._endpoint_key(
                {"id": "pinned"}, "AlloyDB Omni", "http://x", "PROD"
            ),
        )


class ForceRefreshEnvTest(unittest.TestCase):

    def tearDown(self):
        os.environ.pop("EVALBENCH_MCP_FORCE_REFRESH", None)

    def test_env_var_forces_a_refresh(self):
        os.environ["EVALBENCH_MCP_FORCE_REFRESH"] = "1"
        self.assertTrue(orchestrator_mod._force_refresh({}))

    def test_config_flag_forces_a_refresh(self):
        self.assertTrue(orchestrator_mod._force_refresh({"force_refresh": True}))

    def test_default_is_no_refresh(self):
        self.assertFalse(orchestrator_mod._force_refresh({}))


class TimestampParsingTest(unittest.TestCase):

    def test_trailing_z_is_accepted(self):
        parsed = baseline_mod._parse_timestamp("2026-09-09T00:00:00Z")
        self.assertEqual(parsed.tzinfo, datetime.timezone.utc)

    def test_empty_is_none(self):
        self.assertIsNone(baseline_mod._parse_timestamp(""))


if __name__ == "__main__":
    unittest.main()
