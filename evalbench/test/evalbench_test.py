import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Under PYTHONPATH=evalbench, evalbench.py is evaluated as the module "evalbench.evalbench"
# or just "evalbench" if we point to it directly. The most reliable way is:
from evalbench.evalbench import eval as evalbench_eval, run_suite, main


class TestEvalbench(unittest.TestCase):

    @patch('evalbench.evalbench.get_orchestrator')
    @patch('evalbench.evalbench.load_yaml_config')
    @patch('evalbench.evalbench.load_dataset_from_json')
    @patch('evalbench.evalbench.set_session_configs')
    @patch('evalbench.evalbench.load_session_configs')
    @patch('evalbench.evalbench.get_reporters')
    def test_eval_success(self, mock_get_reporters, mock_load_session, mock_set_session, mock_load_dataset, mock_load_yaml, mock_get_orch):
        mock_load_yaml.return_value = {"dummy": "config"}

        def mock_set_session_fn(session, parsed_config):
            session["dataset_config"] = "fake_ds.json"
        mock_set_session.side_effect = mock_set_session_fn

        mock_load_session.return_value = ({}, [{}], {}, {})

        mock_evaluator = MagicMock()
        mock_evaluator.process.return_value = ("job123", "10s", None, None, None)
        mock_get_orch.return_value = mock_evaluator

        success = evalbench_eval("fake_config.yaml")

        self.assertTrue(success)
        mock_evaluator.evaluate.assert_called_once()
        mock_evaluator.process.assert_called_once()

    @patch('evalbench.evalbench.get_orchestrator')
    @patch('evalbench.evalbench.load_yaml_config')
    def test_eval_error(self, mock_load_yaml, mock_get_orch):
        mock_load_yaml.side_effect = Exception("Config parse error")
        success = evalbench_eval("fake_config.yaml")
        self.assertFalse(success)

    @patch('evalbench.evalbench.eval')
    @patch('yaml.safe_load')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_run_suite_success(self, mock_open, mock_yaml_load, mock_eval):
        mock_yaml_load.return_value = {
            "name": "Test Suite",
            "runs": [
                {"name": "Run 1", "config_path": "path1.yaml"},
                {"name": "Run 2", "config_path": "path2.yaml"}
            ]
        }
        mock_eval.side_effect = [True, True]

        success = run_suite("suite.yaml")

        self.assertTrue(success)
        self.assertEqual(mock_eval.call_count, 2)
        mock_eval.assert_any_call("path1.yaml")
        mock_eval.assert_any_call("path2.yaml")

    @patch('evalbench.evalbench.eval')
    @patch('yaml.safe_load')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_run_suite_failure(self, mock_open, mock_yaml_load, mock_eval):
        mock_yaml_load.return_value = {
            "name": "Test Suite",
            "runs": [
                {"name": "Run 1", "config_path": "path1.yaml"},
                {"name": "Run 2", "config_path": "path2.yaml"}
            ]
        }
        mock_eval.side_effect = [True, False]

        success = run_suite("suite.yaml")

        self.assertFalse(success)

    @patch('evalbench.evalbench.run_script')
    @patch('os.path.exists')
    @patch('evalbench.evalbench.get_orchestrator')
    @patch('evalbench.evalbench.load_yaml_config')
    @patch('evalbench.evalbench.load_dataset_from_json')
    @patch('evalbench.evalbench.set_session_configs')
    @patch('evalbench.evalbench.load_session_configs')
    @patch('evalbench.evalbench.get_reporters')
    def test_eval_teardown_on_success(self, mock_get_reporters, mock_load_session, mock_set_session, mock_load_dataset, mock_load_yaml, mock_get_orch, mock_exists, mock_run_script):
        mock_load_yaml.return_value = {"dummy": "config"}

        def mock_set_session_fn(session, parsed_config):
            session["dataset_config"] = "fake_ds.json"
        mock_set_session.side_effect = mock_set_session_fn

        mock_load_session.return_value = ({"tear_down_script": "teardown.sh"}, [{}], {}, {})

        mock_evaluator = MagicMock()
        mock_evaluator.job_id = "job123"
        mock_evaluator.process.return_value = ("job123", "10s", None, None, None)
        mock_get_orch.return_value = mock_evaluator

        mock_exists.return_value = True

        success = evalbench_eval("fake_config.yaml")

        self.assertTrue(success)
        mock_run_script.assert_called_once_with("teardown.sh", os.path.abspath("results/job123"), "teardown")

    @patch('evalbench.evalbench.run_script')
    @patch('os.path.exists')
    @patch('evalbench.evalbench.get_orchestrator')
    @patch('evalbench.evalbench.load_yaml_config')
    @patch('evalbench.evalbench.load_dataset_from_json')
    @patch('evalbench.evalbench.set_session_configs')
    @patch('evalbench.evalbench.load_session_configs')
    def test_eval_teardown_on_error(self, mock_load_session, mock_set_session, mock_load_dataset, mock_load_yaml, mock_get_orch, mock_exists, mock_run_script):
        mock_load_yaml.return_value = {"dummy": "config"}

        def mock_set_session_fn(session, parsed_config):
            session["dataset_config"] = "fake_ds.json"
        mock_set_session.side_effect = mock_set_session_fn

        mock_load_session.return_value = ({"tear_down_script": "teardown.sh"}, [{}], {}, {})

        mock_evaluator = MagicMock()
        mock_evaluator.job_id = "job123"
        mock_evaluator.evaluate.side_effect = Exception("Evaluation failed!")
        mock_get_orch.return_value = mock_evaluator

        mock_exists.return_value = True

        success = evalbench_eval("fake_config.yaml")

        self.assertFalse(success)
        mock_run_script.assert_called_once_with("teardown.sh", os.path.abspath("results/job123"), "teardown")


class BigQueryReporter(MagicMock):
    """A reporter whose class name the failure list reports."""


class CsvReporter(MagicMock):
    """A reporter whose class name the failure list reports."""


class TestStoreReports(unittest.TestCase):

    def test_failed_reporter_does_not_stop_the_others(self):
        from evalbench.evalbench import _store_reports

        bigquery = BigQueryReporter()
        bigquery.store.side_effect = RuntimeError("403 Access Denied")
        csv_reporter = CsvReporter()

        failed = _store_reports(
            [bigquery, csv_reporter], "configs", "results", "scores", "summary")

        self.assertEqual(["BigQueryReporter"], failed)
        self.assertEqual(4, csv_reporter.store.call_count)
        csv_reporter.print_dashboard_links.assert_called_once()

    def test_no_failures_returns_an_empty_list(self):
        from evalbench.evalbench import _store_reports

        self.assertEqual([], _store_reports(
            [CsvReporter()], "configs", "results", "scores", "summary"))

    @patch('evalbench.evalbench._store_reports',
           return_value=["BigQueryReporter"])
    @patch('evalbench.evalbench.load_json', return_value=[])
    @patch('evalbench.evalbench.analyzer.analyze_result')
    @patch('evalbench.evalbench.config_to_df')
    @patch('evalbench.evalbench.get_orchestrator')
    @patch('evalbench.evalbench.load_yaml_config')
    @patch('evalbench.evalbench.load_dataset_from_json')
    @patch('evalbench.evalbench.set_session_configs')
    @patch('evalbench.evalbench.load_session_configs')
    @patch('evalbench.evalbench.get_reporters')
    def test_eval_fails_when_a_reporter_fails(
            self, mock_get_reporters, mock_load_session, mock_set_session,
            mock_load_dataset, mock_load_yaml, mock_get_orch,
            mock_config_to_df, mock_analyze, mock_load_json, mock_store):
        import pandas as pd

        mock_load_yaml.return_value = {"dummy": "config"}
        mock_set_session.side_effect = (
            lambda session, parsed_config: session.update(
                {"dataset_config": "fake_ds.json"}))
        mock_load_session.return_value = ({}, [{}], {}, {})
        mock_analyze.return_value = (pd.DataFrame(), pd.DataFrame())
        mock_evaluator = MagicMock()
        mock_evaluator.process.return_value = (
            "job123", "10s", "results.json", "scores.json", None)
        mock_get_orch.return_value = mock_evaluator

        success = evalbench_eval("fake_config.yaml")

        self.assertFalse(success)
        mock_store.assert_called_once()


if __name__ == '__main__':
    unittest.main()
