"""Tests for the viewer's trends precompute.

The precompute runs against a results directory that grew past 20k run
directories in production, where a pass that held everything in memory and only
wrote at the end was killed before it ever finished. These tests pin the
properties that keep that from recurring: work happens in bounded batches, each
batch is durably checkpointed, and a kill part way through loses only the batch
in flight.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

# Add viewer directory and root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../viewer")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from viewer import precompute_trends


def _row(job_id, product="prod", requester="req", dataset="ds.json"):
    """A process_directory return value, with the fields precompute reads."""
    return {
        "run_time": "2026-08-05 00:00:00",
        "requester": requester,
        "product": product,
        "dataset": dataset,
        "model_config.generator": "gen",
        "latency": 1.0,
        "tokens": 2.0,
        "trajectory": 3.0,
        "executable": 4.0,
        "turn_count": 5.0,
        "exact_match": 6.0,
        "llmrater": 7.0,
        "goal_completion": 8.0,
        "job_id": job_id,
        "ai_score": 9.0,
        "ai_summary": "summary",
    }


class PrecomputeTrendsTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.results_dir = self.temp_dir.name

        patcher = patch.object(
            precompute_trends, "get_results_dir", return_value=self.results_dir
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_runs(self, count, prefix="run"):
        job_ids = [f"{prefix}-{i}" for i in range(count)]
        for job_id in job_ids:
            os.makedirs(os.path.join(self.results_dir, job_id))
        return job_ids

    def _path(self, name):
        return os.path.join(self.results_dir, name)

    def _cache(self):
        return pd.read_csv(self._path("trends_cache.csv"))

    def _processed(self):
        with open(self._path("processed_dirs.json")) as f:
            return json.load(f)

    def _filters(self):
        with open(self._path("filters_cache.json")) as f:
            return json.load(f)

    def test_all_runs_land_in_the_cache_across_batches(self):
        job_ids = self._make_runs(7)
        with patch.object(precompute_trends, "BATCH_SIZE", 2), patch.object(
            precompute_trends, "process_directory", side_effect=lambda d, _: _row(d)
        ):
            precompute_trends.precompute()

        self.assertCountEqual(job_ids, self._cache()["job_id"].tolist())
        self.assertCountEqual(job_ids, self._processed())

    def test_each_batch_is_checkpointed_before_the_next_one_starts(self):
        """A pass killed mid-way must leave the completed batches on disk."""
        self._make_runs(6)
        seen_at_batch_boundary = []

        def record_then_process(directory, _):
            # Sampled on entry to each unit of work, so it reflects what a kill
            # arriving at that moment would have left behind.
            if os.path.exists(self._path("processed_dirs.json")):
                seen_at_batch_boundary.append(len(self._processed()))
            else:
                seen_at_batch_boundary.append(0)
            return _row(directory)

        with patch.object(precompute_trends, "BATCH_SIZE", 2), patch.object(
            precompute_trends, "process_directory", side_effect=record_then_process
        ):
            precompute_trends.precompute()

        # Nothing checkpointed during batch 1, then 2 and 4 rows before the
        # second and third batches: progress is durable as it goes.
        self.assertEqual([0, 0, 2, 2, 4, 4], seen_at_batch_boundary)

    def test_a_killed_pass_resumes_from_the_last_checkpoint(self):
        self._make_runs(6)
        boom = RuntimeError("SIGKILL stand-in")

        calls = []

        def fail_in_third_batch(directory, _):
            calls.append(directory)
            if len(calls) > 4:
                raise boom
            return _row(directory)

        with patch.object(precompute_trends, "BATCH_SIZE", 2), patch.object(
            precompute_trends, "process_directory", side_effect=fail_in_third_batch
        ):
            # process_directory raising is caught per directory, so the pass
            # itself completes; the two failed runs stay unprocessed.
            precompute_trends.precompute()

        self.assertEqual(4, len(self._cache()))
        self.assertEqual(4, len(self._processed()))

        # The next pass picks up exactly what was left.
        with patch.object(precompute_trends, "BATCH_SIZE", 2), patch.object(
            precompute_trends, "process_directory", side_effect=lambda d, _: _row(d)
        ):
            precompute_trends.precompute()

        self.assertEqual(6, len(self._cache()))
        self.assertEqual(6, len(self._processed()))
        self.assertEqual(6, self._cache()["job_id"].nunique())

    def test_appending_never_reads_the_existing_rows_back(self):
        """The cache is 57MB in production; loading it per batch is what this avoids."""
        self._make_runs(4)
        full_reads = []
        real_read_csv = pd.read_csv

        def spy(path, *args, **kwargs):
            if str(path).endswith("trends_cache.csv"):
                # nrows=0 reads the header only; usecols reads one column.
                if kwargs.get("nrows") != 0 and "usecols" not in kwargs:
                    full_reads.append(path)
            return real_read_csv(path, *args, **kwargs)

        with patch.object(precompute_trends, "BATCH_SIZE", 2), patch.object(
            precompute_trends, "process_directory", side_effect=lambda d, _: _row(d)
        ), patch.object(precompute_trends.pd, "read_csv", side_effect=spy):
            precompute_trends.precompute()

        self.assertEqual([], full_reads)

    def test_a_run_still_in_flight_is_left_for_a_later_pass(self):
        self._make_runs(2, prefix="done")
        self._make_runs(1, prefix="running")

        def skip_running(directory, _):
            return None if directory.startswith("running") else _row(directory)

        with patch.object(
            precompute_trends, "process_directory", side_effect=skip_running
        ):
            precompute_trends.precompute()

        self.assertCountEqual(["done-0", "done-1"], self._processed())
        self.assertNotIn("running-0", self._processed())

    def test_one_unreadable_run_does_not_cost_the_rest_of_its_batch(self):
        self._make_runs(4)

        def fail_on_one(directory, _):
            if directory == "run-2":
                raise OSError("transient FUSE error")
            return _row(directory)

        with patch.object(precompute_trends, "BATCH_SIZE", 4), patch.object(
            precompute_trends, "process_directory", side_effect=fail_on_one
        ):
            precompute_trends.precompute()

        self.assertCountEqual(
            ["run-0", "run-1", "run-3"], self._cache()["job_id"].tolist()
        )
        self.assertNotIn("run-2", self._processed())

    def test_filter_values_accumulate_across_passes(self):
        self._make_runs(1, prefix="first")
        with patch.object(
            precompute_trends,
            "process_directory",
            side_effect=lambda d, _: _row(d, product="A", requester="ann", dataset="a.json"),
        ):
            precompute_trends.precompute()

        self._make_runs(1, prefix="second")
        with patch.object(
            precompute_trends,
            "process_directory",
            side_effect=lambda d, _: _row(d, product="B", requester="bob", dataset="b.json"),
        ):
            precompute_trends.precompute()

        filters = self._filters()
        self.assertEqual(["A", "B"], filters["products"])
        self.assertEqual(["ann", "bob"], filters["requesters"])
        self.assertEqual(["a.json", "b.json"], filters["datasets"])
        self.assertCountEqual(["first-0", "second-0"], filters["eval_ids"])

    def test_filters_are_only_rewritten_when_they_change(self):
        """Most batches add no new product or requester; rewriting is pure churn."""
        self._make_runs(6)
        writes = []
        real_write_json = precompute_trends._write_json

        def spy(path, data):
            if str(path).endswith("filters_cache.json"):
                writes.append(path)
            return real_write_json(path, data)

        with patch.object(precompute_trends, "BATCH_SIZE", 2), patch.object(
            precompute_trends, "process_directory", side_effect=lambda d, _: _row(d)
        ), patch.object(precompute_trends, "_write_json", side_effect=spy):
            precompute_trends.precompute()

        # Three batches, but every run carries the same product/requester/dataset,
        # so only the first batch changes anything.
        self.assertEqual(1, len(writes))

    def test_unknown_filter_values_are_not_offered_as_choices(self):
        self._make_runs(1)
        with patch.object(
            precompute_trends,
            "process_directory",
            side_effect=lambda d, _: _row(
                d, product="unknown", requester="unknown", dataset="unknown"
            ),
        ):
            precompute_trends.precompute()

        filters = self._filters()
        self.assertEqual([], filters["products"])
        self.assertEqual([], filters["requesters"])
        self.assertEqual([], filters["datasets"])

    def test_a_run_already_in_the_cache_is_not_appended_twice(self):
        """processed_dirs.json can be cleared on its own from the viewer."""
        self._make_runs(2)
        with patch.object(
            precompute_trends, "process_directory", side_effect=lambda d, _: _row(d)
        ):
            precompute_trends.precompute()
            os.remove(self._path("processed_dirs.json"))
            precompute_trends.precompute()

        self.assertEqual(2, len(self._cache()))

    def test_no_new_directories_writes_nothing(self):
        self._make_runs(1)
        with patch.object(
            precompute_trends, "process_directory", side_effect=lambda d, _: _row(d)
        ):
            precompute_trends.precompute()

        before = os.path.getmtime(self._path("trends_cache.csv"))
        with patch.object(
            precompute_trends, "process_directory", side_effect=AssertionError
        ):
            precompute_trends.precompute()

        self.assertEqual(before, os.path.getmtime(self._path("trends_cache.csv")))

    def test_a_truncated_cache_is_started_over_rather_than_appended_to(self):
        self._make_runs(1)
        open(self._path("trends_cache.csv"), "w").close()

        with patch.object(
            precompute_trends, "process_directory", side_effect=lambda d, _: _row(d)
        ):
            precompute_trends.precompute()

        self.assertEqual(["run-0"], self._cache()["job_id"].tolist())

    def test_a_changed_row_schema_rewrites_the_cache_under_one_header(self):
        self._make_runs(1, prefix="old")
        with patch.object(
            precompute_trends, "process_directory", side_effect=lambda d, _: _row(d)
        ):
            precompute_trends.precompute()

        def with_new_field(directory, _):
            row = _row(directory)
            row["new_metric"] = 1.0
            return row

        self._make_runs(1, prefix="new")
        with patch.object(
            precompute_trends, "process_directory", side_effect=with_new_field
        ):
            precompute_trends.precompute()

        cache = self._cache()
        self.assertCountEqual(["old-0", "new-0"], cache["job_id"].tolist())
        self.assertIn("new_metric", cache.columns)


class ProcessDirectoryTest(unittest.TestCase):
    """The per-run read, which is where the memory was actually spent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.results_dir = self.temp_dir.name
        self.run_dir = os.path.join(self.results_dir, "run-0")
        os.makedirs(self.run_dir)

        # The summarizer makes a live model call per run; irrelevant here.
        patcher = patch.dict(
            sys.modules, {"summarizer": unittest.mock.MagicMock()}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self._write("configs.csv", pd.DataFrame([
            {"config": "experiment_config.product_name", "value": "Cloud SQL"},
            {"config": "experiment_config.dataset_config", "value": "datasets/x/y.json"},
            {"config": "model_config.generator", "value": "gemini"},
        ]))

    def _write(self, name, df):
        df.to_csv(os.path.join(self.run_dir, name), index=False)

    def _write_summary(self, include_goal_completion):
        rows = [{
            "metric_name": "end_to_end_latency",
            "metric_score": 1.5,
            "correct_results_count": 0,
            "total_results_count": 0,
            "run_time": "2026-08-05T00:00:00",
        }]
        if include_goal_completion:
            rows.append({
                "metric_name": "goal_completion",
                "metric_score": 0,
                "correct_results_count": 3,
                "total_results_count": 4,
                "run_time": "2026-08-05T00:00:00",
            })
        self._write("summary.csv", pd.DataFrame(rows))

    def test_reads_the_metrics_a_run_reports(self):
        self._write_summary(include_goal_completion=True)

        res = precompute_trends.process_directory("run-0", self.results_dir)

        self.assertEqual("Cloud SQL", res["product"])
        self.assertEqual("y.json", res["dataset"])
        self.assertEqual("gemini", res["model_config.generator"])
        self.assertEqual(1.5, res["latency"])
        self.assertEqual(75.0, res["goal_completion"])

    def test_goal_completion_falls_back_to_scores_without_reading_the_log_blobs(self):
        """scores.csv rows carry comparison_logs far larger than the scores."""
        self._write_summary(include_goal_completion=False)
        self._write("scores.csv", pd.DataFrame([
            {"comparator": "goal_completion", "score": 100.0,
             "comparison_logs": "x" * 100_000},
            {"comparator": "goal_completion", "score": 0.0,
             "comparison_logs": "x" * 100_000},
        ]))

        read_columns = []
        real_read_csv = pd.read_csv

        def spy(path, *args, **kwargs):
            if str(path).endswith("scores.csv") and kwargs.get("nrows") != 0:
                read_columns.append(kwargs.get("usecols"))
            return real_read_csv(path, *args, **kwargs)

        with patch.object(precompute_trends.pd, "read_csv", side_effect=spy):
            res = precompute_trends.process_directory("run-0", self.results_dir)

        self.assertEqual(50.0, res["goal_completion"])
        self.assertEqual([["comparator", "score"]], read_columns)

    def test_a_scores_file_without_the_expected_columns_is_left_alone(self):
        self._write_summary(include_goal_completion=False)
        self._write("scores.csv", pd.DataFrame([{"something_else": 1}]))

        res = precompute_trends.process_directory("run-0", self.results_dir)

        self.assertEqual(0.0, res["goal_completion"])

    def test_a_run_without_a_summary_is_skipped(self):
        self.assertIsNone(
            precompute_trends.process_directory("run-0", self.results_dir)
        )


if __name__ == "__main__":
    unittest.main()
