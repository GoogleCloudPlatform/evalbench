import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from evaluator.agentevaluator import AgentEvaluator
from generators.models.agent_cli import AgentCliGenerator


class TestAgentEvaluatorEnvSetup(unittest.TestCase):
    """Tests for AgentEvaluator environment file setup logic."""

    def setUp(self):
        # Two separate trees, as in gRPC mode: the evaluator-only resources
        # root (where the client's env_files are uploaded) and the sandbox.
        self.test_dir = tempfile.mkdtemp()
        self.session_id = "test_session_123"
        self.resources_root = os.path.join(self.test_dir, "resources")
        self.session_dir = os.path.join(self.resources_root, self.session_id)
        self.fake_home = os.path.join(
            self.test_dir, "sandbox", self.session_id, "fake_home")

        self._patch_root = patch(
            "evaluator.agentevaluator.SESSION_RESOURCES_PATH",
            self.resources_root,
        )
        self._patch_root.start()

        # Create the simulated upload directory structure
        self.upload_env_dir = os.path.join(
            self.session_dir, "env_files", "env"
        )
        os.makedirs(self.upload_env_dir, exist_ok=True)

        # Create a dummy env file in the upload directory
        self.dummy_file_path = os.path.join(self.upload_env_dir, "sleep.py")
        with open(self.dummy_file_path, "w") as f:
            f.write("print('mock sleep')")

    def tearDown(self):
        self._patch_root.stop()
        # Clean up the temporary directory
        shutil.rmtree(self.test_dir)

    def _make_evaluator(self, mock_get_generator):
        mock_generator = MagicMock(spec=AgentCliGenerator)
        # Set fake_home on the generator to point to our temp fake_home
        mock_generator.fake_home = self.fake_home
        mock_generator.name = "mock_agent_cli"
        mock_get_generator.return_value = mock_generator

        # session_id is what eval_service.Eval sets on the run config in
        # gRPC mode; it keys the resources root.
        config = {
            "model_config": "dummy_model_config.yaml",
            "runners": {"agent_runners": 1},
            "session_id": self.session_id,
        }
        evaluator = AgentEvaluator(config)

        # Mock the generator's generate/safe_generate to avoid actual CLI execution
        mock_result = MagicMock()
        mock_result.stdout = '{"session_id": "test_session_123"}'
        mock_result.stderr = ''
        mock_result.returncode = 0
        mock_generator.safe_generate.return_value = mock_result
        mock_generator.create_command.return_value = ["dummy_cmd"]
        mock_generator.parse_response.return_value = {
            "session_id": "test_session_123"
        }
        mock_generator.extract_tools.return_value = []
        mock_generator.extract_skills.return_value = []

        # Mock finalize_scenario to avoid scoring
        evaluator._finalize_scenario = MagicMock()
        return evaluator, mock_generator

    @patch("evaluator.agentevaluator.get_generator")
    def test_process_scenario_copies_env_files(self, mock_get_generator):
        evaluator, _ = self._make_evaluator(mock_get_generator)

        # Define the scenario with env_files
        scenario = {
            "id": "test_scenario",
            "starting_prompt": "run sleep.py",
            "max_turns": 1,
            "env_files": ["env/sleep.py"]
        }

        # Run process_scenario
        eval_result = MagicMock()
        evaluator.process_scenario(
            scenario=scenario,
            eval_result=eval_result,
            job_id="job1",
            metadata={}
        )

        # Verify the file was copied into the sandbox
        expected_copied_path = os.path.join(self.fake_home, "env/sleep.py")
        self.assertTrue(os.path.exists(expected_copied_path))
        with open(expected_copied_path, "r") as f:
            content = f.read()
        self.assertEqual(content, "print('mock sleep')")

    @patch("evaluator.agentevaluator.get_generator")
    def test_work_dir_inside_resources_is_copied_into_sandbox(
            self, mock_get_generator):
        """A relative scenario work_dir resolves next to the dataset file,
        i.e. inside the evaluator-only resources tree. The agent must get a
        copy inside its sandbox as cwd, never the original."""
        evaluator, mock_generator = self._make_evaluator(mock_get_generator)

        work_dir = os.path.join(
            self.session_dir, "google3", "scenarios", "fixtures")
        os.makedirs(work_dir)
        with open(os.path.join(work_dir, "seed.sql"), "w") as f:
            f.write("select 1;")
        # An answer key sitting next to the fixtures, as in the real layout.
        with open(os.path.join(
                self.session_dir, "google3", "scenarios", "rubric.yaml"),
                "w") as f:
            f.write("rubric_items: []")

        scenario = {
            "id": "test_scenario",
            "starting_prompt": "do it",
            "max_turns": 1,
            "resolved_work_dir": work_dir,
        }
        evaluator.process_scenario(
            scenario=scenario, eval_result=MagicMock(), job_id="job1",
            metadata={},
        )

        cwd = mock_generator.create_command.call_args.kwargs["cwd"]
        expected_cwd = os.path.join(self.fake_home, "fixtures")
        self.assertEqual(cwd, expected_cwd)
        self.assertTrue(os.path.exists(os.path.join(expected_cwd, "seed.sql")))
        # Nothing reachable by a relative path from cwd leads to the rubric.
        self.assertFalse(os.path.exists(
            os.path.join(expected_cwd, "..", "rubric.yaml")))
        self.assertFalse(
            os.path.abspath(cwd).startswith(self.resources_root))

    @patch("evaluator.agentevaluator.get_generator")
    def test_work_dir_outside_resources_is_left_alone(
            self, mock_get_generator):
        evaluator, mock_generator = self._make_evaluator(mock_get_generator)

        work_dir = os.path.join(self.test_dir, "elsewhere", "project")
        scenario = {
            "id": "test_scenario",
            "starting_prompt": "do it",
            "max_turns": 1,
            "resolved_work_dir": work_dir,
        }
        evaluator.process_scenario(
            scenario=scenario, eval_result=MagicMock(), job_id="job1",
            metadata={},
        )

        cwd = mock_generator.create_command.call_args.kwargs["cwd"]
        self.assertEqual(cwd, work_dir)
        self.assertTrue(os.path.isdir(work_dir))


if __name__ == "__main__":
    unittest.main()
