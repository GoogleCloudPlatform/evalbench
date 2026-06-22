"""Unit tests for Dataform scorers."""

import os
import unittest
from unittest.mock import MagicMock, patch

from scorers.dataformscorer import DataformCompileScorer, DataformRunScorer


class TestDataformScorers(unittest.TestCase):

    def setUp(self):
        self.config = {}
        self.compile_scorer = DataformCompileScorer(self.config)
        self.run_scorer = DataformRunScorer(self.config)

        # Clear environment variables to avoid pollution from host
        self.env_patcher = patch.dict(os.environ, {}, clear=True)
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    @patch("scorers.dataformscorer.os.walk")
    def test_find_project_dir_success(self, mock_walk):
        mock_walk.return_value = [
            ("/tmp/fake_home", ["definitions"], ["workflow_settings.yaml"]),
        ]
        project_dir = self.compile_scorer._find_project_dir("/tmp/fake_home")
        self.assertEqual(project_dir, "/tmp/fake_home")

    @patch("scorers.dataformscorer.os.walk")
    def test_find_project_dir_depth_limit(self, mock_walk):
        mock_walk.return_value = [
            ("/tmp/fake_home/a/b/c/d/e", [], ["workflow_settings.yaml"]),
        ]
        project_dir = self.compile_scorer._find_project_dir("/tmp/fake_home")
        self.assertIsNone(project_dir)

    @patch("scorers.dataformscorer.os.walk")
    def test_find_project_dir_exact_depth_limit(self, mock_walk):
        # Mock os.walk to yield hierarchy up to depth 3
        mock_walk.return_value = [
            ("/tmp/fake_home", ["a"], []),
            ("/tmp/fake_home/a", ["b"], []),
            ("/tmp/fake_home/a/b", ["c"], []),
            ("/tmp/fake_home/a/b/c", [], ["workflow_settings.yaml"]),
        ]
        project_dir = self.compile_scorer._find_project_dir("/tmp/fake_home")
        self.assertEqual(project_dir, "/tmp/fake_home/a/b/c")

    def test_get_env_var_source_and_value_scenario(self):
        scenario_env = {"MY_VAR": "scenario_val"}
        source, val = self.compile_scorer._get_env_var_source_and_value("MY_VAR", scenario_env)
        self.assertEqual(source, "scenario")
        self.assertEqual(val, "scenario_val")

    @patch.dict(os.environ, {"MY_VAR": "host_val"})
    def test_get_env_var_source_and_value_host(self):
        scenario_env = {}
        source, val = self.compile_scorer._get_env_var_source_and_value("MY_VAR", scenario_env)
        self.assertEqual(source, "host")
        self.assertEqual(val, "host_val")

    def test_get_env_var_source_and_value_none(self):
        scenario_env = {}
        source, val = self.compile_scorer._get_env_var_source_and_value("MY_VAR", scenario_env)
        self.assertIsNone(source)
        self.assertIsNone(val)

    def test_resolve_env_var_from_scenario(self):
        scenario_env = {"TEST_VAR": "val1"}
        target_env = {}
        log_messages = []
        self.compile_scorer._resolve_env_var("TEST_VAR", scenario_env, target_env, log_messages)
        self.assertEqual(target_env["TEST_VAR"], "val1")
        self.assertTrue(any("got TEST_VAR from scenario" in msg for msg in log_messages))

    @patch.dict(os.environ, {"TEST_VAR": "val2"})
    def test_resolve_env_var_from_host(self):
        scenario_env = {}
        target_env = {}
        log_messages = []
        self.compile_scorer._resolve_env_var("TEST_VAR", scenario_env, target_env, log_messages)
        self.assertEqual(target_env["TEST_VAR"], "val2")
        self.assertTrue(any("got TEST_VAR from host" in msg for msg in log_messages))

    def test_resolve_env_var_missing(self):
        scenario_env = {}
        target_env = {}
        log_messages = []
        self.compile_scorer._resolve_env_var("TEST_VAR", scenario_env, target_env, log_messages)
        self.assertNotIn("TEST_VAR", target_env)
        self.assertTrue(any("TEST_VAR is missing" in msg for msg in log_messages))

    @patch("scorers.dataformscorer.subprocess.run")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_setup_credentials_success(self, mock_open, mock_run):
        mock_process = MagicMock()
        mock_process.stdout = "us-east1\n"
        mock_run.return_value = mock_process

        project_id = "my-project"
        env = {"CLOUDSDK_CORE_PROJECT": project_id}

        resolved_project = self.compile_scorer._setup_credentials("/tmp/project", project_id, env=env)

        self.assertEqual(resolved_project, project_id)
        mock_run.assert_called_once_with(
            ["gcloud", "config", "get-value", "compute/region"],
            capture_output=True, text=True, check=True,
            env=env
        )
        mock_open.assert_called_once_with("/tmp/project/.df-credentials.json", "w")
        # Verify content written
        handle = mock_open()
        written_content = handle.write.call_args[0][0]
        self.assertIn('"projectId": "my-project"', written_content)
        self.assertIn('"location": "us-east1"', written_content)

    @patch("scorers.dataformscorer.os.walk")
    @patch("scorers.dataformscorer.subprocess.run")
    def test_run_dataform_command_env_inheritance_scenario(self, mock_run, mock_walk):
        mock_walk.return_value = [
            ("/tmp/project", [], ["workflow_settings.yaml"]),
        ]
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Success"
        mock_run.return_value = mock_process

        generated_eval_result = {
            "fake_home": "/tmp",
            "scenario": {
                "env": {
                    "CLOUDSDK_CORE_PROJECT": "scenario-sdk-proj",
                    "GOOGLE_CLOUD_PROJECT": "scenario-gcp-proj"
                }
            }
        }

        score, message = self.compile_scorer.compare(
            nl_prompt="test",
            golden_query="test",
            query_type="test",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="test",
            generated_execution_result=[],
            generated_eval_result=generated_eval_result,
            generated_error="",
        )

        self.assertEqual(score, 100.0)
        self.assertIn("got CLOUDSDK_CORE_PROJECT from scenario: 'scenario-sdk-proj'", message)
        self.assertIn("got GOOGLE_CLOUD_PROJECT from scenario: 'scenario-gcp-proj'", message)

        # Verify subprocess.run was called with inherited env
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        subprocess_env = kwargs["env"]
        self.assertEqual(subprocess_env["CLOUDSDK_CORE_PROJECT"], "scenario-sdk-proj")
        self.assertEqual(subprocess_env["GOOGLE_CLOUD_PROJECT"], "scenario-gcp-proj")

    @patch("scorers.dataformscorer.os.walk")
    @patch("scorers.dataformscorer.subprocess.run")
    def test_run_dataform_command_env_inheritance_host(self, mock_run, mock_walk):
        mock_walk.return_value = [
            ("/tmp/project", [], ["workflow_settings.yaml"]),
        ]
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Success"
        mock_run.return_value = mock_process

        # Set host env
        os.environ["CLOUDSDK_CORE_PROJECT"] = "host-sdk-proj"
        os.environ["GOOGLE_CLOUD_PROJECT"] = "host-gcp-proj"

        generated_eval_result = {
            "fake_home": "/tmp",
            "scenario": {
                "env": {}  # Empty scenario env
            }
        }

        score, message = self.compile_scorer.compare(
            nl_prompt="test",
            golden_query="test",
            query_type="test",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="test",
            generated_execution_result=[],
            generated_eval_result=generated_eval_result,
            generated_error="",
        )

        self.assertEqual(score, 100.0)
        self.assertIn("got CLOUDSDK_CORE_PROJECT from host: 'host-sdk-proj'", message)
        self.assertIn("got GOOGLE_CLOUD_PROJECT from host: 'host-gcp-proj'", message)

        # Verify subprocess.run was called with host env
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        subprocess_env = kwargs["env"]
        self.assertEqual(subprocess_env["CLOUDSDK_CORE_PROJECT"], "host-sdk-proj")
        self.assertEqual(subprocess_env["GOOGLE_CLOUD_PROJECT"], "host-gcp-proj")

    @patch("scorers.dataformscorer.os.walk")
    def test_run_dataform_command_missing_all_fails(self, mock_walk):
        mock_walk.return_value = [
            ("/tmp/project", [], ["workflow_settings.yaml"]),
        ]

        # No scenario env, and host env is cleared by setUp patcher
        generated_eval_result = {
            "fake_home": "/tmp",
            "scenario": {
                "env": {}
            }
        }

        score, message = self.compile_scorer.compare(
            nl_prompt="test",
            golden_query="test",
            query_type="test",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="test",
            generated_execution_result=[],
            generated_eval_result=generated_eval_result,
            generated_error="",
        )

        self.assertEqual(score, 0.0)
        self.assertIn("Error: Both CLOUDSDK_CORE_PROJECT and GOOGLE_CLOUD_PROJECT are missing", message)


if __name__ == "__main__":
    unittest.main()
