import datetime
import math
import unittest
from unittest.mock import MagicMock, patch

from google.api_core.exceptions import Forbidden, NotFound, ServiceUnavailable
from google.cloud import bigquery
import pandas as pd
from reporting.bqstore import BigQueryReporter
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
                    "value": None,
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
        self.assertEqual(schema_types["run_time"], "DATETIME")

        _, rows = self.mock_client.insert_rows_json.call_args[0]
        self.assertEqual(rows[0]["correct_results_count"], 2)
        self.assertEqual(rows[0]["metric_score"], 100)
        self.assertEqual(rows[0]["total_results_count"], 2)

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
        self.assertIsNone(rows[1]["score"])
        self.assertEqual(rows[1]["dialects"], [])

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


if __name__ == "__main__":
    unittest.main()
