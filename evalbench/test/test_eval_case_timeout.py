import concurrent.futures
import subprocess
import time
import unittest
from unittest.mock import MagicMock, patch

from evaluator.agentevaluator import AgentEvaluator
from evaluator.evaluator import Evaluator, _process_futures_with_timeout
from generators.models.agent_cli import AgentCliGenerator
from generators.models.gemini_cli import GeminiCliGenerator
from generators.models.codex_cli import CodexCliGenerator
from generators.models.claude_code import ClaudeCodeGenerator
from generators.models.agy_cli import AgyCliGenerator
from util.config import get_eval_case_timeout, parse_timeout_seconds
from work.agentscorework import AgentScoreWork


class TestEvalCaseTimeoutConfig(unittest.TestCase):
    """Unit tests for parse_timeout_seconds and get_eval_case_timeout."""

    def test_parse_timeout_seconds_numeric(self):
        self.assertEqual(parse_timeout_seconds(300), 300.0)
        self.assertEqual(parse_timeout_seconds(12.5), 12.5)
        self.assertEqual(parse_timeout_seconds(0), 0.0)
        with self.assertRaises(ValueError):
            parse_timeout_seconds(-10)
        self.assertIsNone(parse_timeout_seconds(None))

    def test_parse_timeout_seconds_strings(self):
        self.assertEqual(parse_timeout_seconds("300"), 300.0)
        self.assertEqual(parse_timeout_seconds("45.5"), 45.5)
        self.assertEqual(parse_timeout_seconds("300s"), 300.0)
        self.assertEqual(parse_timeout_seconds("5m"), 300.0)
        self.assertEqual(parse_timeout_seconds("1h"), 3600.0)
        self.assertEqual(parse_timeout_seconds("1h30m"), 5400.0)
        self.assertEqual(parse_timeout_seconds("1h\t30m"), 5400.0)
        self.assertEqual(parse_timeout_seconds("5m 30s"), 330.0)
        self.assertEqual(parse_timeout_seconds("10  s"), 10.0)
        self.assertEqual(parse_timeout_seconds("1d"), 86400.0)
        self.assertEqual(parse_timeout_seconds("500ms"), 0.5)
        self.assertEqual(parse_timeout_seconds(" 10m "), 600.0)

    def test_parse_timeout_seconds_invalid(self):
        with self.assertRaises(ValueError):
            parse_timeout_seconds("invalid")
        self.assertIsNone(parse_timeout_seconds(""))
        with self.assertRaises(TypeError):
            parse_timeout_seconds([])
        with self.assertRaises(TypeError):
            parse_timeout_seconds({})
        with self.assertRaises(TypeError):
            parse_timeout_seconds(True)
        with self.assertRaises(ValueError):
            parse_timeout_seconds("1h and 30m")

    def test_get_eval_case_timeout_top_level(self):
        self.assertEqual(
            get_eval_case_timeout({"eval_case_timeout": 300}), 300.0
        )
        self.assertEqual(
            get_eval_case_timeout({"eval_case_timeout": "5m"}), 300.0
        )
        self.assertEqual(
            get_eval_case_timeout({"eval_case_timeout": "1h30m"}), 5400.0
        )

    def test_get_eval_case_timeout_default_none(self):
        self.assertIsNone(get_eval_case_timeout({}))
        self.assertIsNone(get_eval_case_timeout({"runners": {}}))
        self.assertIsNone(get_eval_case_timeout({"runners": {"task_timeout_seconds": 400}}))
        self.assertIsNone(get_eval_case_timeout({"orchestrator": "geminicli"}))


class TestAgentEvaluatorTimeout(unittest.TestCase):
    """Unit tests for AgentEvaluator timeout enforcement."""

    @patch("evaluator.agentevaluator.get_generator")
    def test_agent_evaluator_initializes_timeout(self, mock_get_generator):
        mock_generator = MagicMock(spec=AgentCliGenerator)
        mock_generator.name = "mock_agent_cli"
        mock_generator.version = "1.0"
        mock_get_generator.return_value = mock_generator

        # With timeout in run_config
        config_with_timeout = {
            "model_config": "dummy.yaml",
            "eval_case_timeout": "300s",
        }
        evaluator = AgentEvaluator(config_with_timeout)
        self.assertEqual(evaluator.eval_case_timeout_seconds, 300.0)

        # Without timeout (default)
        config_without_timeout = {
            "model_config": "dummy.yaml",
        }
        evaluator_default = AgentEvaluator(config_without_timeout)
        self.assertIsNone(evaluator_default.eval_case_timeout_seconds)

    @patch("evaluator.agentevaluator.get_generator")
    def test_process_scenario_times_out_multi_turn(self, mock_get_generator):
        mock_generator = MagicMock(spec=AgentCliGenerator)
        mock_generator.name = "mock_agent_cli"
        mock_generator.version = "1.0"
        mock_generator.fake_home = "/tmp/fake_home"
        mock_generator.create_command.return_value = MagicMock()
        mock_generator.parse_response.return_value = {"session_id": "s1"}
        mock_generator.extract_tools.return_value = []
        mock_generator.extract_skills.return_value = []
        mock_get_generator.return_value = mock_generator

        config = {
            "model_config": "dummy.yaml",
            "eval_case_timeout": 0.05,  # 50ms timeout
        }
        evaluator = AgentEvaluator(config)
        evaluator._finalize_scenario = MagicMock()

        # Generator simulates a slow execution
        def slow_safe_generate(cli_cmd, timeout_seconds=None):
            time.sleep(0.08)
            return subprocess.CompletedProcess(
                args=["mock"], returncode=0, stdout='{"response": "ok"}', stderr=""
            )

        mock_generator.safe_generate.side_effect = slow_safe_generate

        simulated_user = MagicMock()
        simulated_user.get_next_response.return_value = "next question"

        scenario = {
            "id": "scenario_timeout_test",
            "starting_prompt": "Hello",
            "max_turns": 3,
        }

        evaluator.process_scenario(
            scenario=scenario,
            eval_result=MagicMock(),
            job_id="job_123",
            metadata={},
            simulated_user=simulated_user,
        )

        # Verify finalize_scenario was called with the result
        evaluator._finalize_scenario.assert_called_once()
        args, kwargs = evaluator._finalize_scenario.call_args
        last_result = args[1]
        self.assertIsNotNone(last_result)
        self.assertEqual(last_result.returncode, 124)
        self.assertIn("TimeoutError", last_result.stderr)
        # Should have only executed 1 turn due to timeout
        self.assertEqual(mock_generator.safe_generate.call_count, 1)

    @patch("evaluator.agentevaluator.get_generator")
    def test_process_scenario_times_out_before_turn_1(self, mock_get_generator):
        mock_generator = MagicMock(spec=AgentCliGenerator)
        mock_generator.name = "mock_agent_cli"
        mock_generator.version = "1.0"
        mock_get_generator.return_value = mock_generator

        config = {
            "model_config": "dummy.yaml",
            "eval_case_timeout": 0.0,  # 0s timeout -> immediately expired
        }
        evaluator = AgentEvaluator(config)
        evaluator._finalize_scenario = MagicMock()

        scenario = {
            "id": "scenario_immediate_timeout",
            "starting_prompt": "Hello",
            "max_turns": 3,
        }

        evaluator.process_scenario(
            scenario=scenario,
            eval_result=MagicMock(),
            job_id="job_123",
            metadata={},
        )

        # Finalize scenario must be called even if timed out before turn 1
        evaluator._finalize_scenario.assert_called_once()
        args, kwargs = evaluator._finalize_scenario.call_args
        last_result = args[1]
        self.assertEqual(last_result.returncode, 124)
        self.assertIn("TimeoutError", last_result.stderr)
        self.assertEqual(mock_generator.safe_generate.call_count, 0)

    @patch("evaluator.agentevaluator.get_generator")
    def test_process_scenario_scenario_level_timeout_override(self, mock_get_generator):
        mock_generator = MagicMock(spec=AgentCliGenerator)
        mock_generator.name = "mock_agent_cli"
        mock_generator.version = "1.0"
        mock_generator.fake_home = "/tmp/fake_home"
        mock_generator.create_command.return_value = MagicMock()
        mock_generator.parse_response.return_value = {"session_id": "s1"}
        mock_generator.extract_tools.return_value = []
        mock_generator.extract_skills.return_value = []
        mock_get_generator.return_value = mock_generator

        # Run config has NO timeout set
        config = {"model_config": "dummy.yaml"}
        evaluator = AgentEvaluator(config)
        evaluator._finalize_scenario = MagicMock()

        def slow_safe_generate(cli_cmd, timeout_seconds=None):
            time.sleep(0.08)
            return subprocess.CompletedProcess(
                args=["mock"], returncode=0, stdout='{"response": "ok"}', stderr=""
            )

        mock_generator.safe_generate.side_effect = slow_safe_generate

        simulated_user = MagicMock()
        simulated_user.get_next_response.return_value = "next question"

        # Scenario overrides timeout locally to 50ms
        scenario = {
            "id": "scenario_override_test",
            "starting_prompt": "Hello",
            "max_turns": 3,
            "eval_case_timeout": "50ms",
        }

        evaluator.process_scenario(
            scenario=scenario,
            eval_result=MagicMock(),
            job_id="job_123",
            metadata={},
            simulated_user=simulated_user,
        )

        # Should have only executed 1 turn due to scenario-level 50ms timeout
        self.assertEqual(mock_generator.safe_generate.call_count, 1)

    @patch("evaluator.agentevaluator.get_generator")
    def test_process_scenario_records_timeout_error_in_result(self, mock_get_generator):
        mock_generator = MagicMock(spec=AgentCliGenerator)
        mock_generator.name = "mock_agent_cli"
        mock_generator.version = "1.0"
        mock_generator.fake_home = "/tmp/fake_home"
        mock_generator.create_command.return_value = MagicMock()
        mock_generator.parse_response.return_value = {}
        mock_generator.extract_tools.return_value = []
        mock_generator.extract_skills.return_value = []
        mock_get_generator.return_value = mock_generator

        config = {
            "model_config": "dummy.yaml",
            "eval_case_timeout": 0.05,
        }
        evaluator = AgentEvaluator(config)

        # Simulate safe_generate returning a TimeoutError
        mock_generator.safe_generate.return_value = subprocess.CompletedProcess(
            args=["mock"],
            returncode=124,
            stdout="",
            stderr="TimeoutError: Command timed out after 0.05 seconds",
        )

        with patch("evaluator.agentevaluator.AgentScoreWork") as mock_score_work:
            mock_instance = MagicMock()
            mock_score_work.return_value = mock_instance

            scenario = {
                "id": "scenario_timeout_err",
                "starting_prompt": "Execute task",
                "max_turns": 2,
            }

            evaluator.process_scenario(
                scenario=scenario,
                eval_result=MagicMock(scoring_results=[]),
                job_id="job_123",
                metadata={},
            )

            # Check that AgentScoreWork received eval_output with job_id, generated_error, and artifacts populated
            mock_score_work.assert_called_once()
            eval_output = mock_score_work.call_args[1]["eval_output"]
            self.assertEqual(eval_output["job_id"], "job_123")
            self.assertIn("TimeoutError", eval_output["stderr"])
            self.assertIn("TimeoutError", eval_output["generated_error"])
            self.assertEqual(eval_output["returncode"], 124)

    def test_agent_score_work_propagates_generated_error(self):
        eval_output = {
            "eval_id": "test_err_prop",
            "generated_error": "TimeoutError: Command timed out after 10s",
            "accumulated_tools": [],
            "scenario": {"starting_prompt": "prompt", "expected_trajectory": []},
            "metadata": {},
            "job_id": "job_123",
        }
        score_work = AgentScoreWork(
            config={},
            eval_output=eval_output,
            scoring_results=[],
        )
        with patch("scorers.score.compare") as mock_compare:
            score_work.run()
            mock_compare.assert_called_once()
            eval_output_item = mock_compare.call_args[1]["eval_output_item"]
            self.assertEqual(
                eval_output_item["generated_error"],
                "TimeoutError: Command timed out after 10s",
            )

    @patch("evaluator.agentevaluator.SimulatedUser")
    @patch("evaluator.agentevaluator.get_generator")
    def test_evaluate_agent_cli_returns_job_id_and_outputs_on_timeout(self, mock_get_generator, mock_sim_user):
        mock_generator = MagicMock(spec=AgentCliGenerator)
        mock_generator.name = "mock_agent_cli"
        mock_generator.version = "1.0"
        mock_generator.fake_home = "/tmp/fake_home"
        mock_generator.create_command.return_value = MagicMock()
        mock_generator.parse_response.return_value = {}
        mock_generator.extract_tools.return_value = []
        mock_generator.extract_skills.return_value = []
        mock_get_generator.return_value = mock_generator

        # Subprocess times out
        mock_generator.safe_generate.return_value = subprocess.CompletedProcess(
            args=["mock"],
            returncode=124,
            stdout="partial stdout output",
            stderr="TimeoutError: Command timed out after 0.05 seconds",
        )

        config = {
            "model_config": "dummy.yaml",
            "simulated_user_model_config": "dummy_user.yaml",
            "eval_case_timeout": 0.05,
            "runners": {"agent_runners": 1},
        }
        evaluator = AgentEvaluator(config)

        import json
        import types
        mock_request = types.SimpleNamespace(
            payload=json.dumps({
                "scenarios": [{
                    "id": "scenario_timeout_upload",
                    "starting_prompt": "Run long task",
                    "max_turns": 3,
                }]
            }),
            agent_results=[],
            scoring_results=[],
        )

        mock_sim_user.return_value.get_next_response.return_value = "TERMINATE"

        with patch("evaluator.agentevaluator.AgentScoreWork"):
            eval_outputs, scoring_results = evaluator._evaluate_agent_cli(
                dataset=[mock_request],
                job_id="job_timeout_456",
                run_time=MagicMock(),
            )

            # Validate that outputs returned for reporting/upload retain job_id and artifacts
            self.assertEqual(len(eval_outputs), 1)
            output = eval_outputs[0]
            self.assertEqual(output["job_id"], "job_timeout_456")
            self.assertEqual(output["eval_id"], "scenario_timeout_upload")
            self.assertEqual(output["returncode"], 124)
            self.assertIn("TimeoutError", output["generated_error"])
            self.assertEqual(output["stdout"], "partial stdout output")


class TestEvaluatorFuturesTimeout(unittest.TestCase):
    """Unit tests for OneShot Evaluator task timeout handling."""

    def test_evaluator_configures_timeout(self):
        config_with_timeout = {
            "eval_case_timeout": "2m",
        }
        evaluator = Evaluator(config_with_timeout)
        self.assertEqual(evaluator.task_timeout_seconds, 120.0)
        self.assertEqual(evaluator.scoring_runners, 10)
        self.assertEqual(evaluator.num_trials, 1)

        config_default = {
            "runners": {"task_timeout_seconds": 450, "scoring_runners": 5},
            "num_trials": 3,
        }
        evaluator_default = Evaluator(config_default)
        self.assertEqual(evaluator_default.task_timeout_seconds, 450.0)
        self.assertEqual(evaluator_default.scoring_runners, 5)
        self.assertEqual(evaluator_default.num_trials, 3)

    def test_process_futures_with_timeout_times_out(self):
        mock_future = concurrent.futures.Future()
        future_map = {mock_future: {"id": "test_1"}}

        # Generator that yields with timeout
        results = list(
            _process_futures_with_timeout(
                [mock_future],
                future_map,
                timeout=0.05,
            )
        )

        self.assertEqual(len(results), 1)
        future, eval_out, timed_out = results[0]
        self.assertTrue(timed_out)
        self.assertEqual(eval_out["id"], "test_1")


class TestGeneratorsTimeout(unittest.TestCase):
    """Unit tests for CLI generators timeout execution."""

    @patch("subprocess.run")
    def test_gemini_cli_run_timeout(self, mock_subprocess_run):
        mock_subprocess_run.side_effect = subprocess.TimeoutExpired(
            cmd=["npm", "exec"], timeout=10.0, output="partial", stderr="err"
        )
        gen = GeminiCliGenerator.__new__(GeminiCliGenerator)
        gen.fake_home = "/tmp/fake_home"
        gen.gemini_home = "/tmp/fake_home/.gemini"
        gen.gemini_cli_version = "@google/gemini-cli@0.36.0"
        gen.env = {}

        result = gen._execute_cli_command(["npm", "exec"], timeout_seconds=10.0)
        self.assertEqual(result.returncode, 124)
        self.assertIn("TimeoutError", result.stderr)

    @patch("subprocess.Popen")
    def test_codex_cli_run_timeout(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["item 1\n", "item 2\n"])
        mock_proc.stderr = iter([])
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd=["codex"], timeout=5.0),
            0,
        ]
        mock_popen.return_value = mock_proc

        gen = CodexCliGenerator.__new__(CodexCliGenerator)
        gen.fake_home = "/tmp/fake_home"

        result, durations = gen._execute_cli_command(["codex"], timeout_seconds=5.0)
        self.assertEqual(result.returncode, 124)
        self.assertIn("TimeoutError", result.stderr)
        mock_proc.kill.assert_called_once()

    @patch("subprocess.run")
    def test_claude_code_run_timeout(self, mock_subprocess_run):
        mock_subprocess_run.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=5.0
        )
        gen = ClaudeCodeGenerator.__new__(ClaudeCodeGenerator)
        gen.fake_home = "/tmp/fake_home"

        result = gen._execute_cli_command(["claude"], timeout_seconds=5.0)
        self.assertEqual(result.returncode, 124)
        self.assertIn("TimeoutError", result.stderr)

    @patch("subprocess.run")
    def test_agy_cli_run_timeout(self, mock_subprocess_run):
        mock_subprocess_run.side_effect = subprocess.TimeoutExpired(
            cmd=["agy"], timeout=5.0
        )
        gen = AgyCliGenerator.__new__(AgyCliGenerator)
        gen.fake_home = "/tmp/fake_home"

        result = gen._execute_cli_command(["agy"], timeout_seconds=5.0)
        self.assertEqual(result.returncode, 124)
        self.assertIn("TimeoutError", result.stderr)


if __name__ == "__main__":
    unittest.main()
