import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from eval_service import _process_results, EmptyEvalResultError


class BigQueryReporter(MagicMock):
    """Fake BigQuery reporter named for _store_reports diagnostics."""


class CsvReporter(MagicMock):
    """Fake CSV reporter named for _store_reports diagnostics."""


class TestEvalServiceProcessResultsReporting(unittest.TestCase):

    def _write_json_tempfile(self, payload) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def test_reporter_failure_does_not_discard_eval_summary_or_block_other_reporters(
        self,
    ):
        results_tf = self._write_json_tempfile([
            {
                "id": "scenario-1",
                "prompt_id": "scenario-1",
                "generated_sql": "",
                "generated_error": None,
            }
        ])
        scores_tf = self._write_json_tempfile([
            {
                "id": "scenario-1",
                "prompt_id": "scenario-1",
                "comparator": "goal_completion",
                "score": 100,
                "generated_sql": "",
                "generated_error": None,
            }
        ])

        bq_reporter = BigQueryReporter()
        bq_reporter.store.side_effect = RuntimeError(
            "403 Quota exceeded: load_job_per_table.long"
        )
        csv_reporter = CsvReporter()

        summary = _process_results(
            reporters=[bq_reporter, csv_reporter],
            job_id="job-abc",
            run_time=datetime.datetime(2026, 11, 18, 3, 20, 0),
            results_tf=results_tf,
            scores_tf=scores_tf,
            multi_trial_scores_tf=None,
            config={
                "orchestrator": "geminicli",
                "scorers": {"goal_completion": {}},
            },
            model_config={"generator": "agy_cli"},
            db_configs=[],
        )

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["scores"]["goal_completion"], 1)
        self.assertEqual(csv_reporter.store.call_count, 4)
        self.assertEqual(len(summary["reporting_errors"]), 1)
        self.assertEqual(
            summary["reporting_errors"][0]["reporter"], "BigQueryReporter"
        )
        self.assertIn(
            "load_job_per_table.long", summary["reporting_errors"][0]["error"]
        )
        self.assertFalse(os.path.exists(results_tf))
        self.assertFalse(os.path.exists(scores_tf))

    def test_empty_results_still_unlinks_tempfiles_and_raises(self):
        results_tf = self._write_json_tempfile([])
        scores_tf = self._write_json_tempfile([])

        with self.assertRaises(EmptyEvalResultError):
            _process_results(
                reporters=[],
                job_id="job-empty",
                run_time=datetime.datetime(2026, 11, 18, 3, 20, 0),
                results_tf=results_tf,
                scores_tf=scores_tf,
                multi_trial_scores_tf=None,
                config={"scorers": {}},
                model_config={},
                db_configs=[],
            )

        self.assertFalse(os.path.exists(results_tf))
        self.assertFalse(os.path.exists(scores_tf))

    @patch("eval_service.get_reporters")
    def test_reporter_init_failure_is_isolated_and_unlinks_tempfiles(
        self, mock_get_reporters
    ):
        results_tf = self._write_json_tempfile([
            {
                "id": "scenario-1",
                "prompt_id": "scenario-1",
                "generated_sql": "",
                "generated_error": None,
            }
        ])
        scores_tf = self._write_json_tempfile([
            {
                "id": "scenario-1",
                "prompt_id": "scenario-1",
                "comparator": "goal_completion",
                "score": 100,
                "generated_sql": "",
                "generated_error": None,
            }
        ])

        csv_reporter = CsvReporter()

        def _get_reporters_side_effect(reporting_cfg, job_id, run_time):
            if "bigquery" in reporting_cfg:
                raise ValueError("GCP project ID not set")
            return [csv_reporter]

        mock_get_reporters.side_effect = _get_reporters_side_effect

        summary = _process_results(
            reporters=None,
            job_id="job-init-err",
            run_time=datetime.datetime(2026, 11, 18, 3, 20, 0),
            results_tf=results_tf,
            scores_tf=scores_tf,
            multi_trial_scores_tf=None,
            config={
                "orchestrator": "geminicli",
                "scorers": {"goal_completion": {}},
                "reporting": {"bigquery": {}, "csv": {"output_directory": "x"}},
            },
            model_config={"generator": "agy_cli"},
            db_configs=[],
            fail_if_all_reporters_failed=True,
        )

        self.assertEqual(summary["total"], 1)
        self.assertEqual(csv_reporter.store.call_count, 4)
        self.assertEqual(len(summary["reporting_errors"]), 1)
        self.assertEqual(summary["reporting_errors"][0]["reporter"], "bigquery")
        self.assertIn("GCP project ID", summary["reporting_errors"][0]["error"])
        self.assertFalse(os.path.exists(results_tf))
        self.assertFalse(os.path.exists(scores_tf))

    def test_all_reporters_failing_raises_when_caller_has_no_summary(self):
        results_tf = self._write_json_tempfile([
            {
                "id": "scenario-1",
                "prompt_id": "scenario-1",
                "generated_sql": "",
                "generated_error": None,
            }
        ])
        scores_tf = self._write_json_tempfile([
            {
                "id": "scenario-1",
                "prompt_id": "scenario-1",
                "comparator": "goal_completion",
                "score": 100,
                "generated_sql": "",
                "generated_error": None,
            }
        ])

        bq_reporter = BigQueryReporter()
        bq_reporter.store.side_effect = RuntimeError("403 Forbidden")

        with self.assertRaises(RuntimeError):
            _process_results(
                reporters=[bq_reporter],
                job_id="job-all-failed",
                run_time=datetime.datetime(2026, 11, 18, 3, 20, 0),
                results_tf=results_tf,
                scores_tf=scores_tf,
                multi_trial_scores_tf=None,
                config={
                    "orchestrator": "geminicli",
                    "scorers": {"goal_completion": {}},
                },
                model_config={"generator": "agy_cli"},
                db_configs=[],
                fail_if_all_reporters_failed=True,
            )

        self.assertFalse(os.path.exists(results_tf))
        self.assertFalse(os.path.exists(scores_tf))


if __name__ == "__main__":
    unittest.main()
