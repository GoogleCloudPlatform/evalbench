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
        # Create a temporary directory for the session
        self.test_dir = tempfile.mkdtemp()
        self.session_id = "test_session_123"
        self.session_dir = os.path.join(self.test_dir, self.session_id)
        self.fake_home = os.path.join(self.session_dir, "fake_home")

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
        # Clean up the temporary directory
        shutil.rmtree(self.test_dir)

    @patch("evaluator.agentevaluator.get_generator")
    def test_process_scenario_copies_env_files(self, mock_get_generator):
        # 1. Setup mocks
        mock_generator = MagicMock(spec=AgentCliGenerator)
        # Set fake_home on the generator to point to our temp fake_home
        mock_generator.fake_home = self.fake_home
        mock_generator.name = "mock_agent_cli"
        mock_get_generator.return_value = mock_generator

        # 2. Initialize AgentEvaluator
        config = {
            "model_config": "dummy_model_config.yaml",
            "runners": {"agent_runners": 1}
        }
        evaluator = AgentEvaluator(config)

        # 3. Define the scenario with env_files
        scenario = {
            "id": "test_scenario",
            "starting_prompt": "run sleep.py",
            "max_turns": 1,
            "env_files": ["env/sleep.py"]
        }

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

        # 4. Run process_scenario
        eval_result = MagicMock()
        evaluator.process_scenario(
            scenario=scenario,
            eval_result=eval_result,
            job_id="job1",
            metadata={}
        )

        # 5. Verify the file was copied into the sandbox
        expected_copied_path = os.path.join(self.fake_home, "env/sleep.py")
        self.assertTrue(os.path.exists(expected_copied_path))
        with open(expected_copied_path, "r") as f:
            content = f.read()
        self.assertEqual(content, "print('mock sleep')")


class TestAgentEvaluatorSandbox(unittest.TestCase):
    """Tests for sandbox setup, trusted folder registration, and cleanup."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace_dir = os.path.join(self.test_dir, "workspace")
        os.makedirs(self.workspace_dir)
        with open(os.path.join(self.workspace_dir, "test.txt"), "w") as f:
            f.write("original content")

        self.fake_home = os.path.join(self.test_dir, "fake_home")
        os.makedirs(self.fake_home)

        config = {
            "model_config": "dummy.yaml",
            "runners": {"agent_runners": 1}
        }
        with patch("evaluator.agentevaluator.get_generator") as mock_get_generator:
            mock_generator = MagicMock(spec=AgentCliGenerator)
            mock_generator.fake_home = self.fake_home
            mock_generator.version = "1.0.0"
            mock_get_generator.return_value = mock_generator
            self.evaluator = AgentEvaluator(config)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_setup_sandbox_creates_temp_dir_and_copies_contents(self):
        execution_cwd, temp_sandbox_dir = self.evaluator._setup_sandbox(self.workspace_dir)
        self.assertIsNotNone(temp_sandbox_dir)
        self.assertTrue(os.path.exists(temp_sandbox_dir))
        self.assertTrue(temp_sandbox_dir.startswith(tempfile.gettempdir()))

        # Check content copy
        copied_file = os.path.join(temp_sandbox_dir, "test.txt")
        self.assertTrue(os.path.exists(copied_file))
        with open(copied_file, "r") as f:
            self.assertEqual(f.read(), "original content")

        # Cleanup sandbox directory created inside test
        shutil.rmtree(temp_sandbox_dir)

    def test_register_trusted_folders_creates_valid_json(self):
        sandbox_dir = os.path.join(self.test_dir, "sandbox")
        os.makedirs(sandbox_dir)

        self.evaluator._register_trusted_folders(self.fake_home, sandbox_dir, self.workspace_dir)

        trusted_folders_path = os.path.join(self.fake_home, ".gemini", "trustedFolders.json")
        self.assertTrue(os.path.exists(trusted_folders_path))

        import json
        with open(trusted_folders_path, "r") as f:
            data = json.load(f)

        self.assertIn(sandbox_dir, data)
        self.assertEqual(data[sandbox_dir], "TRUST_FOLDER")
        self.assertIn(self.workspace_dir, data)
        self.assertEqual(data[self.workspace_dir], "TRUST_FOLDER")

    def test_cleanup_sandbox_copies_back_and_removes_temp_dir(self):
        # Setup sandbox manually
        temp_sandbox_dir = tempfile.mkdtemp()
        shutil.copytree(self.workspace_dir, temp_sandbox_dir, dirs_exist_ok=True)

        # Modify file inside sandbox
        with open(os.path.join(temp_sandbox_dir, "test.txt"), "w") as f:
            f.write("modified content")
        # Add new file inside sandbox
        with open(os.path.join(temp_sandbox_dir, "new.txt"), "w") as f:
            f.write("new file")

        # Run cleanup
        self.evaluator._cleanup_sandbox(self.workspace_dir, temp_sandbox_dir)

        # Verify temp sandbox is removed
        self.assertFalse(os.path.exists(temp_sandbox_dir))

        # Verify changes were copied back to original workspace
        with open(os.path.join(self.workspace_dir, "test.txt"), "r") as f:
            self.assertEqual(f.read(), "modified content")
        with open(os.path.join(self.workspace_dir, "new.txt"), "r") as f:
            self.assertEqual(f.read(), "new file")


if __name__ == "__main__":
    unittest.main()
