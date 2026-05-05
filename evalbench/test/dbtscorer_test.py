"""Unit tests for dbt scorers."""

import json
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from scorers.dbtscorer import DbtCompileScorer, DbtRunScorer


class TestDbtScorers(unittest.TestCase):

    def setUp(self):
        self.config = {}
        self.compile_scorer = DbtCompileScorer(self.config)
        self.run_scorer = DbtRunScorer(self.config)
        self.generated_eval_result = json.dumps({"fake_home": "/tmp/fake_home"})

    @patch("scorers.dbtscorer.os.walk")
    def test_find_project_dir_success(self, mock_walk):
        # Mock os.walk to find dbt_project.yml
        mock_walk.return_value = [
            ("/tmp/fake_home", ["models"], ["dbt_project.yml"]),
        ]
        project_dir = self.compile_scorer._find_project_dir("/tmp/fake_home")
        self.assertEqual(project_dir, "/tmp/fake_home")

    @patch("scorers.dbtscorer.os.walk")
    def test_find_project_dir_depth_limit(self, mock_walk):
        # Mock os.walk with depth > 3 (e.g. depth 4)
        mock_walk.return_value = [
            ("/tmp/fake_home/a/b/c/d/e", [], ["dbt_project.yml"]),
        ]
        project_dir = self.compile_scorer._find_project_dir("/tmp/fake_home")
        self.assertIsNone(project_dir)

    @patch("scorers.dbtscorer.os.path.exists")
    def test_has_profiles_yml(self, mock_exists):
        mock_exists.return_value = True
        self.assertTrue(self.compile_scorer._has_profiles_yml("/tmp/fake_home"))

        mock_exists.return_value = False
        self.assertFalse(
            self.compile_scorer._has_profiles_yml("/tmp/fake_home")
        )

    @patch("scorers.dbtscorer.shutil.which")
    def test_check_dbt(self, mock_which):
        mock_which.return_value = "/usr/bin/dbt"
        self.assertTrue(self.compile_scorer._check_dbt())

        mock_which.return_value = None
        self.assertFalse(self.compile_scorer._check_dbt())

    @patch("scorers.dbtscorer.shutil.which")
    @patch("scorers.dbtscorer.os.walk")
    @patch("scorers.dbtscorer.subprocess.run")
    @patch("scorers.dbtscorer.os.path.exists")
    def test_dbt_compile_success(
        self, mock_exists, mock_run, mock_walk, mock_which
    ):
        # Setup mocks
        mock_which.return_value = "/usr/bin/dbt"
        mock_walk.return_value = [
            ("/tmp/fake_home", [], ["dbt_project.yml", "profiles.yml"]),
        ]
        mock_exists.return_value = True  # for profiles.yml
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Compile successful"
        mock_run.return_value = mock_process

        score, message = self.compile_scorer.compare(
            nl_prompt="test",
            golden_query="test",
            query_type="test",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="test",
            generated_execution_result=[],
            generated_eval_result=self.generated_eval_result,
            generated_error="",
        )

        self.assertEqual(score, 100.0)
        self.assertIn("succeeded", message)
        # Verify command includes --project-dir and --profiles-dir
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertIn("--project-dir", cmd)
        self.assertIn("--profiles-dir", cmd)
        self.assertEqual(kwargs["cwd"], "/tmp/fake_home")

    @patch("scorers.dbtscorer.shutil.which")
    @patch("scorers.dbtscorer.os.walk")
    @patch("scorers.dbtscorer.subprocess.run")
    @patch("scorers.dbtscorer.os.path.exists")
    def test_dbt_run_fail(self, mock_exists, mock_run, mock_walk, mock_which):
        # Setup mocks
        mock_which.return_value = "/usr/bin/dbt"
        mock_walk.return_value = [
            ("/tmp/fake_home", [], ["dbt_project.yml"]),
        ]
        mock_exists.return_value = False  # no profiles.yml
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.stdout = "Run failed"
        mock_process.stderr = "Error: database connection failed"
        mock_run.return_value = mock_process

        score, message = self.run_scorer.compare(
            nl_prompt="test",
            golden_query="test",
            query_type="test",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="test",
            generated_execution_result=[],
            generated_eval_result=self.generated_eval_result,
            generated_error="",
        )

        self.assertEqual(score, 0.0)
        self.assertIn("failed", message)
        self.assertIn("Error: database connection failed", message)
        # Verify command includes --project-dir but NOT --profiles-dir
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        cmd = args[0]
        self.assertIn("--project-dir", cmd)
        self.assertNotIn("--profiles-dir", cmd)

    @patch("scorers.dbtscorer.shutil.which")
    @patch("scorers.dbtscorer.os.walk")
    @patch("scorers.dbtscorer.subprocess.run")
    @patch("scorers.dbtscorer.os.path.exists")
    def test_dbt_compile_timeout(
        self, mock_exists, mock_run, mock_walk, mock_which
    ):
        # Setup mocks
        mock_which.return_value = "/usr/bin/dbt"
        mock_walk.return_value = [
            ("/tmp/fake_home", [], ["dbt_project.yml"]),
        ]
        mock_exists.return_value = False
        # Simulate timeout
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="dbt compile", timeout=300
        )

        score, message = self.compile_scorer.compare(
            nl_prompt="test",
            golden_query="test",
            query_type="test",
            golden_execution_result=[],
            golden_eval_result="",
            golden_error="",
            generated_query="test",
            generated_execution_result=[],
            generated_eval_result=self.generated_eval_result,
            generated_error="",
        )

        self.assertEqual(score, 0.0)
        self.assertIn("timed out", message)


if __name__ == "__main__":
    unittest.main()
