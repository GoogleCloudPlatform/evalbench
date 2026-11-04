import subprocess
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from evaluator.agentevaluator import AgentEvaluator, _infra_error
from generators.models.agent_cli import AgentCliGenerator
from reporting.analyzer import analyze_one_metric
from work.agentscorework import AgentScoreWork


def _result(returncode, stderr=""):
    return subprocess.CompletedProcess([], returncode, "agent output", stderr)


@pytest.mark.parametrize("returncode, stderr, is_infra", [
    (1, "Error: 429 Too Many Requests", True),
    (1, "status: RESOURCE_EXHAUSTED", True),
    (1, "HTTP 503 Service Unavailable", True),
    (1, "API status code 502", True),
    (1, "Quota exceeded for aiplatform requests", True),
    (124, "TimeoutError after 503 seconds", False),
    (0, "Error 429", False),
    (1, "Tool failed: table not found", False),
    (1, 'File "tool.py", line 503, in run', False),
    (1, "WARNING: credentials without a quota project", False),
], ids=["429", "resource_exhausted", "503", "status_code_502",
        "quota_exceeded", "timeout", "success", "agent_failure",
        "traceback_line_number", "quota_project_warning"])
def test_infra_error_detection(returncode, stderr, is_infra):
    assert bool(_infra_error(_result(returncode, stderr))) == is_infra


@pytest.fixture
def run_scenario():
    """Runs one 3-turn scenario and returns (generated_error, simulated_user)."""
    patcher = patch("evaluator.agentevaluator.get_generator")
    generator = MagicMock(spec=AgentCliGenerator)
    generator.version = "1.0"
    generator.name = "mock_cli"
    generator.fake_home = None
    generator.parse_response.return_value = {}
    generator.extract_tools.return_value = []
    generator.extract_skills.return_value = []
    patcher.start().return_value = generator

    def _run(cli_result, simulated_user_reply):
        generator.safe_generate.return_value = cli_result
        simulated_user = MagicMock()
        if isinstance(simulated_user_reply, Exception):
            simulated_user.get_next_response.side_effect = simulated_user_reply
        else:
            simulated_user.get_next_response.return_value = simulated_user_reply
        evaluator = AgentEvaluator({"model_config": "dummy.yaml"})
        evaluator._finalize_scenario = MagicMock()
        evaluator.process_scenario(
            {"id": "s1", "starting_prompt": "hi", "max_turns": 3},
            eval_result=MagicMock(), job_id="job1", metadata={},
            simulated_user=simulated_user)
        return evaluator._finalize_scenario.call_args.args[-1], simulated_user

    yield _run
    patcher.stop()


def test_infra_error_stops_the_conversation(run_scenario):
    error, simulated_user = run_scenario(_result(1, "429 Too Many Requests"),
                                         "next")
    assert error.startswith("Agent infrastructure error (exit 1)")
    simulated_user.get_next_response.assert_not_called()


def test_simulated_user_error_is_recorded(run_scenario):
    error, _ = run_scenario(_result(0), RuntimeError("model down"))
    assert error == "Simulated user error: model down"


def test_empty_simulated_user_reply_continues(run_scenario):
    """The noop simulated user of proxy flows returns "" by design."""
    error, simulated_user = run_scenario(_result(0), "")
    assert error is None
    assert simulated_user.get_next_response.call_count == 2


def test_normal_run_has_no_error(run_scenario):
    error, _ = run_scenario(_result(0), "TERMINATE")
    assert error is None


def test_score_work_passes_generated_error():
    eval_output = {"eval_id": "s1", "generated_error": "Agent infrastructure error",
                   "scenario": {}, "metadata": {}}
    with patch("scorers.score.compare") as mock_compare:
        AgentScoreWork(config={}, eval_output=eval_output,
                       scoring_results=[]).run()
    item = mock_compare.call_args.kwargs["eval_output_item"]
    assert item["generated_error"] == "Agent infrastructure error"


@pytest.mark.parametrize("generated_error", ["Agent infrastructure error", None])
def test_finalize_scenario_writes_generated_error(generated_error):
    with patch("evaluator.agentevaluator.get_generator") as mock_get_generator:
        mock_get_generator.return_value = MagicMock(spec=AgentCliGenerator)
        evaluator = AgentEvaluator({"model_config": "dummy.yaml"})
    eval_result = MagicMock(agent_results=[], scoring_results=[])
    with patch("evaluator.agentevaluator.AgentScoreWork"):
        evaluator._finalize_scenario(
            {"id": "s1", "starting_prompt": "hi"}, _result(1), [], [], [],
            eval_result, "job1", {}, generated_error)
    assert eval_result.agent_results[0]["generated_error"] == generated_error


def test_average_skips_rows_with_generated_error():
    df = pd.DataFrame([
        {"comparator": "end_to_end_latency", "score": 1000.0,
         "generated_error": None},
        {"comparator": "end_to_end_latency", "score": 0.0,
         "generated_error": "Agent infrastructure error"},
    ])
    summary = analyze_one_metric(df, "end_to_end_latency", 100)
    assert summary["metric_score"] == 1000.0
