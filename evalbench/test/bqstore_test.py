import datetime
import json
import math
import unittest
from unittest.mock import MagicMock, patch

from google.api_core.exceptions import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    ServiceUnavailable,
)
from google.cloud import bigquery
import numpy as np
import pandas as pd
from reporting.bqstore import (
    BigQueryReporter,
    _coerce_json_value,
    _dataframe_to_json_rows,
    _infer_schema,
    _is_transient_insert_error,
    _iter_streaming_batches,
)
from reporting.report import STORETYPE


class TestBigQueryReporterStreaming(unittest.TestCase):

    def setUp(self):
        self.run_time = datetime.datetime(2026, 11, 18, 3, 15, 0)
        self.mock_client_patcher = patch("reporting.bqstore.bigquery.Client")
        self.mock_bq_client_cls = self.mock_client_patcher.start()
        self.mock_client = MagicMock()
        self.mock_bq_client_cls.return_value = self.mock_client

        self.reporter = BigQueryReporter(
            {"gcp_project_id": "test-project", "dataset_id": "evalbench"},
            job_id="job-123",
            run_time=self.run_time,
        )

    def tearDown(self):
        self.mock_client_patcher.stop()

    def test_store_uses_streaming_insert_rows_json_and_zero_load_jobs(self):
        configs_schema = [
            bigquery.SchemaField("job_id", "STRING"),
            bigquery.SchemaField("run_time", "DATETIME"),
            bigquery.SchemaField("config", "STRING"),
            bigquery.SchemaField("value", "STRING"),
        ]
        table_obj = bigquery.Table(
            "test-project.evalbench.configs", schema=configs_schema
        )
        self.mock_client.get_table.return_value = table_obj
        self.mock_client.insert_rows_json.return_value = []

        df = pd.DataFrame([
            {
                "job_id": "job-123",
                "run_time": self.run_time,
                "config": "model_config.generator",
                "value": "agy_cli",
            },
            {
                "job_id": "job-123",
                "run_time": self.run_time,
                "config": "experiment_config.empty_val",
                "value": pd.NA,
            },
        ])

        self.reporter.store(df, STORETYPE.CONFIGS)

        self.mock_client.load_table_from_dataframe.assert_not_called()
        self.mock_client.insert_rows_json.assert_called_once()
        target_table, rows = self.mock_client.insert_rows_json.call_args[0]
        self.assertEqual(target_table, "test-project.evalbench.configs")
        self.assertEqual(
            rows,
            [
                {
                    "job_id": "job-123",
                    "run_time": "2026-11-18T03:15:00.000000",
                    "config": "model_config.generator",
                    "value": "agy_cli",
                },
                {
                    "job_id": "job-123",
                    "run_time": "2026-11-18T03:15:00.000000",
                    "config": "experiment_config.empty_val",
                },
            ],
        )

    def test_store_creates_missing_table_and_rounds_summary_integers(self):
        self.mock_client.get_table.side_effect = NotFound("Table not found")

        def _create_table(table_arg, exists_ok=False):
            return table_arg

        self.mock_client.create_table.side_effect = _create_table
        self.mock_client.insert_rows_json.return_value = []

        summary_df = pd.DataFrame([
            {
                "metric_name": "goal_completion",
                "metric_score": 100.0,
                "correct_results_count": 1.67,
                "total_results_count": 2,
                "extra_ratio": 0.75,
                "job_id": "job-123",
                "run_time": self.run_time,
            }
        ])

        self.reporter.store(summary_df, STORETYPE.SUMMARY)

        self.mock_client.create_table.assert_called_once()
        created_table = self.mock_client.create_table.call_args[0][0]
        schema_types = {f.name: f.field_type for f in created_table.schema}
        self.assertEqual(schema_types["metric_score"], "INTEGER")
        self.assertEqual(schema_types["correct_results_count"], "INTEGER")
        self.assertEqual(schema_types["total_results_count"], "INTEGER")
        self.assertEqual(schema_types["extra_ratio"], "STRING")
        self.assertEqual(schema_types["run_time"], "DATETIME")

        _, rows = self.mock_client.insert_rows_json.call_args[0]
        self.assertEqual(rows[0]["correct_results_count"], 2)
        self.assertEqual(rows[0]["metric_score"], 100)
        self.assertEqual(rows[0]["total_results_count"], 2)
        self.assertEqual(rows[0]["extra_ratio"], "0.75")

    def test_store_adds_missing_columns_to_existing_table_schema(self):
        existing_table = bigquery.Table(
            "test-project.evalbench.results",
            schema=[
                bigquery.SchemaField("job_id", "STRING"),
                bigquery.SchemaField("eval_id", "STRING"),
            ],
        )
        updated_table = bigquery.Table(
            "test-project.evalbench.results",
            schema=[
                bigquery.SchemaField("job_id", "STRING"),
                bigquery.SchemaField("eval_id", "STRING"),
                bigquery.SchemaField("accumulated_skills", "STRING"),
            ],
        )
        self.mock_client.get_table.side_effect = [existing_table, existing_table]
        self.mock_client.update_table.return_value = updated_table
        self.mock_client.insert_rows_json.return_value = []

        results_df = pd.DataFrame([
            {
                "job_id": "job-123",
                "eval_id": "scenario-1",
                "accumulated_skills": "['bigquery']",
            }
        ])

        self.reporter.store(results_df, STORETYPE.EVALS)

        self.mock_client.update_table.assert_called_once()
        patched_table = self.mock_client.update_table.call_args[0][0]
        self.assertEqual(
            [f.name for f in patched_table.schema],
            ["job_id", "eval_id", "accumulated_skills"],
        )
        _, rows = self.mock_client.insert_rows_json.call_args[0]
        self.assertEqual(rows[0]["accumulated_skills"], "['bigquery']")

    def test_store_handles_repeated_fields_and_float_nan_safely(self):
        scores_table = bigquery.Table(
            "test-project.evalbench.scores",
            schema=[
                bigquery.SchemaField("id", "STRING"),
                bigquery.SchemaField("comparator", "STRING"),
                bigquery.SchemaField("score", "FLOAT"),
                bigquery.SchemaField("dialects", "STRING", mode="REPEATED"),
                bigquery.SchemaField("job_id", "STRING"),
            ],
        )
        self.mock_client.get_table.return_value = scores_table
        self.mock_client.insert_rows_json.return_value = []

        scores_df = pd.DataFrame([
            {
                "id": "s1",
                "comparator": "goal_completion",
                "score": 95.5,
                "dialects": ["bigquery"],
                "job_id": "job-123",
            },
            {
                "id": "s2",
                "comparator": "goal_completion",
                "score": math.nan,
                "dialects": None,
                "job_id": "job-123",
            },
        ])

        self.reporter.store(scores_df, STORETYPE.SCORES)

        _, rows = self.mock_client.insert_rows_json.call_args[0]
        self.assertEqual(rows[0]["score"], 95.5)
        self.assertEqual(rows[0]["dialects"], ["bigquery"])
        self.assertNotIn("score", rows[1])
        self.assertNotIn("dialects", rows[1])

    @patch("reporting.bqstore.time.sleep")
    def test_store_retries_transient_errors_and_schema_lag_but_not_forbidden(
        self, mock_sleep
    ):
        table_obj = bigquery.Table(
            "test-project.evalbench.configs",
            schema=[
                bigquery.SchemaField("job_id", "STRING"),
                bigquery.SchemaField("config", "STRING"),
            ],
        )
        self.mock_client.get_table.return_value = table_obj
        self.mock_client.insert_rows_json.side_effect = [
            ServiceUnavailable("503 backend transient"),
            [{
                "index": 0,
                "errors": [{"reason": "invalid", "message": "no such field: config."}],
            }],
            [],
        ]

        df = pd.DataFrame([{"job_id": "job-123", "config": "k"}])
        self.reporter.store(df, STORETYPE.CONFIGS)
        self.assertEqual(self.mock_client.insert_rows_json.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

        # Permanent 403 Forbidden (e.g. IAM deny or hard quotaExceeded) fails
        # immediately without sleeping or retrying.
        mock_sleep.reset_mock()
        self.mock_client.insert_rows_json.side_effect = Forbidden(
            "403 Access Denied"
        )
        with self.assertRaises(Forbidden):
            self.reporter.store(df, STORETYPE.CONFIGS)
        mock_sleep.assert_not_called()

    @patch("reporting.bqstore.time.sleep")
    def test_store_retries_multi_row_stopped_siblings_with_stable_row_ids(
        self, mock_sleep
    ):
        table_obj = bigquery.Table(
            "test-project.evalbench.configs",
            schema=[
                bigquery.SchemaField("job_id", "STRING"),
                bigquery.SchemaField("config", "STRING"),
            ],
        )
        self.mock_client.get_table.return_value = table_obj
        self.mock_client.insert_rows_json.side_effect = [
            [
                {
                    "index": 0,
                    "errors": [{
                        "reason": "invalid",
                        "message": "no such field: config.",
                    }],
                },
                {
                    "index": 1,
                    "errors": [{"reason": "stopped", "message": ""}],
                },
            ],
            [],
        ]

        df = pd.DataFrame([
            {"job_id": "job-123", "config": "k1"},
            {"job_id": "job-123", "config": "k2"},
        ])
        self.reporter.store(df, STORETYPE.CONFIGS)

        self.assertEqual(self.mock_client.insert_rows_json.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)
        first_call = self.mock_client.insert_rows_json.call_args_list[0]
        second_call = self.mock_client.insert_rows_json.call_args_list[1]
        self.assertEqual(len(first_call.kwargs["row_ids"]), 2)
        self.assertEqual(
            first_call.kwargs["row_ids"], second_call.kwargs["row_ids"]
        )

    @patch("reporting.bqstore.time.sleep")
    def test_store_partial_transient_error_retries_only_failed_rows_without_get_table(
        self, mock_sleep
    ):
        table_obj = bigquery.Table(
            "test-project.evalbench.configs",
            schema=[
                bigquery.SchemaField("job_id", "STRING"),
                bigquery.SchemaField("config", "STRING"),
            ],
        )
        self.mock_client.get_table.return_value = table_obj
        self.mock_client.insert_rows_json.side_effect = [
            [{
                "index": 1,
                "errors": [{"reason": "backendError", "message": "try again"}],
            }],
            [],
        ]

        df = pd.DataFrame([
            {"job_id": "job-123", "config": "k0"},
            {"job_id": "job-123", "config": "k1"},
            {"job_id": "job-123", "config": "k2"},
        ])
        self.reporter.store(df, STORETYPE.CONFIGS)

        self.assertEqual(self.mock_client.insert_rows_json.call_count, 2)
        # Pure transient backendError must not trigger extra get_table RPCs.
        self.assertEqual(self.mock_client.get_table.call_count, 1)
        first_call = self.mock_client.insert_rows_json.call_args_list[0]
        second_call = self.mock_client.insert_rows_json.call_args_list[1]
        self.assertEqual(len(second_call.args[1]), 1)
        self.assertEqual(second_call.args[1][0]["config"], "k1")
        self.assertEqual(
            second_call.kwargs["row_ids"], [first_call.kwargs["row_ids"][1]]
        )

    @patch("reporting.bqstore.time.sleep")
    def test_store_permanent_row_error_with_stopped_sibling_fails_fast(
        self, mock_sleep
    ):
        table_obj = bigquery.Table(
            "test-project.evalbench.configs",
            schema=[
                bigquery.SchemaField("job_id", "STRING"),
                bigquery.SchemaField("config", "STRING"),
            ],
        )
        self.mock_client.get_table.return_value = table_obj
        self.mock_client.insert_rows_json.return_value = [
            {
                "index": 0,
                "errors": [{"reason": "invalid", "message": "bad integer"}],
            },
            {
                "index": 1,
                "errors": [{"reason": "stopped", "message": ""}],
            },
        ]

        df = pd.DataFrame([
            {"job_id": "job-123", "config": "k0"},
            {"job_id": "job-123", "config": "k1"},
        ])
        with self.assertRaises(RuntimeError):
            self.reporter.store(df, STORETYPE.CONFIGS)
        self.assertEqual(self.mock_client.insert_rows_json.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("reporting.bqstore.time.sleep")
    def test_store_re_coerces_pending_rows_when_schema_changes_on_not_found(
        self, mock_sleep
    ):
        old_table = bigquery.Table(
            "test-project.evalbench.scores",
            schema=[
                bigquery.SchemaField("id", "STRING"),
                bigquery.SchemaField("score", "STRING"),
            ],
        )
        recreated_table = bigquery.Table(
            "test-project.evalbench.scores",
            schema=[
                bigquery.SchemaField("id", "STRING"),
                bigquery.SchemaField("score", "INTEGER"),
            ],
        )
        self.mock_client.get_table.side_effect = [old_table, recreated_table]
        self.mock_client.insert_rows_json.side_effect = [
            NotFound("Table was recreated"),
            [],
        ]

        df = pd.DataFrame([{"id": "s1", "score": 95.6}])
        self.reporter.store(df, STORETYPE.SCORES)

        self.assertEqual(self.mock_client.insert_rows_json.call_count, 2)
        first_rows = self.mock_client.insert_rows_json.call_args_list[0].args[1]
        second_rows = self.mock_client.insert_rows_json.call_args_list[1].args[1]
        self.assertEqual(first_rows[0]["score"], "95.6")
        self.assertEqual(second_rows[0]["score"], 96)

    @patch("reporting.bqstore.time.sleep")
    def test_store_raises_not_found_after_exhausting_retries(self, mock_sleep):
        table_obj = bigquery.Table(
            "test-project.evalbench.configs",
            schema=[bigquery.SchemaField("job_id", "STRING")],
        )
        self.mock_client.get_table.return_value = table_obj
        self.mock_client.insert_rows_json.side_effect = NotFound("missing")

        df = pd.DataFrame([{"job_id": "job-123"}])
        with self.assertRaises(NotFound):
            self.reporter.store(df, STORETYPE.CONFIGS)
        self.assertEqual(self.mock_client.insert_rows_json.call_count, 5)
        self.assertEqual(mock_sleep.call_count, 4)

    @patch("reporting.bqstore.time.sleep")
    def test_update_table_retries_multiple_concurrent_conflicts(self, mock_sleep):
        final_table = bigquery.Table(
            "test-project.evalbench.results",
            schema=[
                bigquery.SchemaField("job_id", "STRING"),
                bigquery.SchemaField("new_col", "STRING"),
            ],
        )
        # Return a fresh Table per get_table() call, matching BigQuery client
        # behavior (update_table mutates table_obj.schema on the local object).
        self.mock_client.get_table.side_effect = lambda _: bigquery.Table(
            "test-project.evalbench.results",
            schema=[bigquery.SchemaField("job_id", "STRING")],
        )
        self.mock_client.update_table.side_effect = [
            PreconditionFailed("etag mismatch 1"),
            Conflict("etag mismatch 2"),
            final_table,
        ]
        self.mock_client.insert_rows_json.return_value = []

        df = pd.DataFrame([{"job_id": "job-123", "new_col": "v"}])
        self.reporter.store(df, STORETYPE.EVALS)

        self.assertEqual(self.mock_client.update_table.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_infer_schema_uses_pandas_gbq_for_full_df_mixed_and_numpy_types(self):
        df = pd.DataFrame({
            "mixed_obj": pd.Series([100, "N/A"], dtype="object"),
            "int_and_float": pd.Series([1, 2.5], dtype="object"),
            "numpy_int": pd.Series([np.int64(7), None], dtype="object"),
            "dialects": pd.Series([None, ["bigquery"]], dtype="object"),
        })
        fields = {f.name: f for f in _infer_schema(df, STORETYPE.EVALS)}
        self.assertEqual(fields["mixed_obj"].field_type, "STRING")
        self.assertEqual(fields["int_and_float"].field_type, "FLOAT")
        self.assertEqual(fields["numpy_int"].field_type, "INTEGER")
        self.assertEqual(fields["dialects"].mode, "REPEATED")
        self.assertEqual(fields["dialects"].field_type, "STRING")

        # All-null in chunk 0 but populated as REPEATED in chunk 1 must infer
        # REPEATED STRING across the whole DataFrame before chunking.
        self.reporter.chunk_size = 1
        self.mock_client.get_table.side_effect = NotFound("missing")
        self.mock_client.create_table.side_effect = (
            lambda table_arg, exists_ok=False: table_arg
        )
        self.mock_client.insert_rows_json.return_value = []
        multi_chunk_df = pd.DataFrame({
            "id": ["s1", "s2"],
            "dialects": pd.Series([None, ["bigquery"]], dtype="object"),
        })
        self.reporter.store(multi_chunk_df, STORETYPE.SCORES)
        created_table = self.mock_client.create_table.call_args[0][0]
        created_fields = {f.name: f for f in created_table.schema}
        self.assertEqual(created_fields["dialects"].mode, "REPEATED")
        self.assertEqual(created_fields["dialects"].field_type, "STRING")

    def test_dataframe_to_json_rows_serialization_edge_cases(self):
        schema = [
            bigquery.SchemaField("big_int", "INT64"),
            bigquery.SchemaField("big_int_str", "INT64"),
            bigquery.SchemaField("dt_offset", "DATETIME"),
            bigquery.SchemaField("dt_empty", "DATETIME"),
            bigquery.SchemaField("ts_empty", "TIMESTAMP"),
            bigquery.SchemaField("d_from_dt", "DATE"),
            bigquery.SchemaField("json_col", "JSON"),
            bigquery.SchemaField("json_str_col", "JSON"),
            bigquery.SchemaField("dict_str", "STRING"),
            bigquery.SchemaField("inf_float", "FLOAT"),
            bigquery.SchemaField(
                "meta",
                "RECORD",
                fields=[
                    bigquery.SchemaField("count", "INT64"),
                    bigquery.SchemaField("tag", "STRING"),
                ],
            ),
        ]
        big_int = 9007199254740993  # 2**53 + 1
        df = pd.DataFrame([
            {
                "big_int": big_int,
                "big_int_str": str(big_int),
                "dt_offset": "2026-11-18T03:15:00-07:00",
                "dt_empty": "",
                "ts_empty": "   ",
                "d_from_dt": self.run_time,
                "json_col": {"a": float("nan"), "b": 1},
                "json_str_col": '{"k": 2}',
                "dict_str": {"nested": "v"},
                "inf_float": float("inf"),
                "meta": {"COUNT": 42, "tag": "ok"},
            },
            {
                "big_int": 1,
                "big_int_str": "",
                "dt_offset": "2026-11-18 03:15:00",
                "dt_empty": None,
                "ts_empty": None,
                "d_from_dt": "",
                "json_col": None,
                "json_str_col": "   ",
                "dict_str": None,
                "inf_float": None,
                "meta": None,
            },
        ])

        rows = _dataframe_to_json_rows(df, schema)
        row = rows[0]
        self.assertEqual(row["big_int"], big_int)
        self.assertEqual(row["big_int_str"], str(big_int))
        self.assertEqual(row["dt_offset"], "2026-11-18T10:15:00.000000")
        self.assertNotIn("dt_empty", row)
        self.assertNotIn("ts_empty", row)
        self.assertEqual(row["d_from_dt"], "2026-11-18")
        self.assertEqual(json.loads(row["json_col"]), {"a": None, "b": 1})
        self.assertEqual(json.loads(row["json_str_col"]), {"k": 2})
        self.assertEqual(json.loads(row["dict_str"]), {"nested": "v"})
        self.assertNotIn("inf_float", row)
        self.assertEqual(row["meta"], {"COUNT": 42, "tag": "ok"})
        # Naive DATETIME in the same column as a tz-offset string must not be shifted.
        self.assertEqual(rows[1]["dt_offset"], "2026-11-18T03:15:00.000000")
        self.assertNotIn("big_int_str", rows[1])
        self.assertNotIn("d_from_dt", rows[1])
        self.assertNotIn("json_str_col", rows[1])

    def test_iter_streaming_batches_splits_by_byte_size(self):
        rows = [
            {"id": "1", "payload": "a" * 80},
            {"id": "2", "payload": "b" * 80},
            {"id": "3", "payload": "c" * 80},
        ]
        batches = list(_iter_streaming_batches(rows, max_rows=10, max_bytes=150))
        self.assertEqual(len(batches), 3)
        self.assertEqual([b[0]["id"] for b in batches], ["1", "2", "3"])

    def test_empty_input_and_unsupported_store_type_and_helper_guards(self):
        self.reporter.store(None, STORETYPE.CONFIGS)
        self.reporter.store(pd.DataFrame(), STORETYPE.CONFIGS)
        self.mock_client.create_dataset.assert_not_called()

        with self.assertRaises(ValueError):
            self.reporter.store(pd.DataFrame([{"a": 1}]), "INVALID_TYPE")

        # Unparseable DATETIME returns raw value; "NaT" maps to None.
        dt_field = bigquery.SchemaField("dt", "DATETIME")
        self.assertEqual(_coerce_json_value("not-a-date", dt_field), "not-a-date")
        self.assertIsNone(_coerce_json_value("NaT", dt_field))

        # Empty error lists and row errors with no inner error entries are non-retryable.
        self.assertFalse(_is_transient_insert_error([]))
        self.assertFalse(_is_transient_insert_error([{"index": 0, "errors": []}]))

    @patch("reporting.bqstore.time.sleep")
    def test_schema_edge_paths_empty_create_race_exhaustion_and_multi_batch_refresh(
        self, mock_sleep
    ):
        # 1. create_table returning empty schema triggers fallback get_table().
        populated_table = bigquery.Table(
            "test-project.evalbench.configs",
            schema=[bigquery.SchemaField("job_id", "STRING")],
        )
        self.mock_client.get_table.side_effect = [
            NotFound("missing"),
            populated_table,
        ]
        self.mock_client.create_table.return_value = bigquery.Table(
            "test-project.evalbench.configs"
        )
        self.mock_client.insert_rows_json.return_value = []
        self.reporter.store(pd.DataFrame([{"job_id": "j1"}]), STORETYPE.CONFIGS)
        self.assertEqual(self.mock_client.get_table.call_count, 2)

        # 2. Another worker adds missing column before update_table -> no update_table call.
        self.reporter._tables.clear()
        self.mock_client.reset_mock()
        stale_table = bigquery.Table(
            "test-project.evalbench.results",
            schema=[bigquery.SchemaField("job_id", "STRING")],
        )
        already_updated = bigquery.Table(
            "test-project.evalbench.results",
            schema=[
                bigquery.SchemaField("job_id", "STRING"),
                bigquery.SchemaField("new_col", "STRING"),
            ],
        )
        self.mock_client.get_table.side_effect = [stale_table, already_updated]
        self.mock_client.insert_rows_json.return_value = []
        self.reporter.store(
            pd.DataFrame([{"job_id": "j1", "new_col": "v"}]), STORETYPE.EVALS
        )
        self.mock_client.update_table.assert_not_called()

        # 3. update_table exhausting all 5 conflict retries re-raises PreconditionFailed.
        self.reporter._tables.clear()
        self.mock_client.reset_mock()
        self.mock_client.get_table.side_effect = lambda _: bigquery.Table(
            "test-project.evalbench.results",
            schema=[bigquery.SchemaField("job_id", "STRING")],
        )
        self.mock_client.update_table.side_effect = PreconditionFailed("etag")
        with self.assertRaises(PreconditionFailed):
            self.reporter.store(
                pd.DataFrame([{"job_id": "j1", "new_col": "v"}]), STORETYPE.EVALS
            )
        self.assertEqual(self.mock_client.update_table.call_count, 5)

        # 4. Schema change during batch 1 of a multi-batch chunk re-coerces batch 2 too.
        self.reporter._tables.clear()
        self.mock_client.reset_mock()
        old_scores = bigquery.Table(
            "test-project.evalbench.scores",
            schema=[
                bigquery.SchemaField("id", "STRING"),
                bigquery.SchemaField("score", "STRING"),
            ],
        )
        new_scores = bigquery.Table(
            "test-project.evalbench.scores",
            schema=[
                bigquery.SchemaField("id", "STRING"),
                bigquery.SchemaField("score", "INTEGER"),
            ],
        )
        self.mock_client.get_table.side_effect = [old_scores, new_scores]
        self.mock_client.insert_rows_json.side_effect = [
            NotFound("recreated"),
            [],
            [],
        ]
        # Force 2 streaming batches within 1 DataFrame chunk via max_bytes patch.
        with patch("reporting.bqstore._MAX_STREAMING_REQUEST_BYTES", 40):
            self.reporter.store(
                pd.DataFrame([
                    {"id": "s1", "score": 95.6},
                    {"id": "s2", "score": 88.2},
                ]),
                STORETYPE.SCORES,
            )
        self.assertEqual(self.mock_client.insert_rows_json.call_count, 3)
        batch1_retry = self.mock_client.insert_rows_json.call_args_list[1].args[1]
        batch2_rows = self.mock_client.insert_rows_json.call_args_list[2].args[1]
        self.assertEqual(batch1_retry[0]["score"], 96)
        self.assertEqual(batch2_rows[0]["score"], 88)


if __name__ == "__main__":
    unittest.main()
