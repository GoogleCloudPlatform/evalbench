import json
import subprocess
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from evaluator.agentevaluator import AgentEvaluator
from generators.models.agent_cli import AgentCliGenerator
from generators.models.claude_code import ClaudeCodeGenerator
from generators.models.codex_cli import CodexCliGenerator
from generators.models.gemini_cli import GeminiCliGenerator
from generators.models.noop_agent import NoopAgentGenerator
from generators.models.passthrough import NOOPGenerator
from reporting.analyzer import analyze_one_metric, analyze_result
from scorers import score as score_module
from work.agentscorework import AgentScoreWork


def _result(returncode, stderr="", stdout="agent output"):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _stream(*events):
    return "\n".join(json.dumps(e) for e in events)


def _bare(cls):
    """Returns a generator without running __init__, which sets up a sandbox."""
    generator = object.__new__(cls)
    generator.model = "test-model"
    generator.pricing = None
    return generator


def _gemini_result(status, message=None, stderr="", error_type="Error"):
    result_event = {"type": "result", "status": status, "stats": {}}
    if message is not None:
        result_event["error"] = {"type": error_type, "message": message}
    stdout = _stream({"type": "init", "session_id": "s"}, result_event)
    return _result(0 if status == "success" else 1, stderr, stdout)


@pytest.mark.parametrize("result, is_infra", [
    (_gemini_result("error", '[API Error: {"error":{"code":429,"status":'
                    '"RESOURCE_EXHAUSTED"}}]\nPlease wait and try again later.'),
     True),
    (_gemini_result("error", "[API Error: got status: 503 Service Unavailable]"),
     True),
    (_gemini_result("error", "[API Error: You have exhausted your daily quota.]"),
     True),
    (_gemini_result("error", "[API Error: Deadline expired before operation "
                    "could complete. (Status: DEADLINE_EXCEEDED)]"), True),
    (_gemini_result("error", "Error executing tool bigquery__execute_sql: "
                    "HTTP 500 UNAVAILABLE"), False),
    (_gemini_result("error", "[API Error: Request contains an invalid argument. "
                    "(Status: INVALID_ARGUMENT)]"), False),
    (_gemini_result("error", "[API Error: The aiplatform.googleapis.com API "
                    "requires a quota project, which is not set by default. "
                    "(Status: PERMISSION_DENIED)]"), False),
    (_gemini_result("success", stderr="Attempt 1 failed with status 429. "
                    "Retrying with backoff..."), False),
    (_gemini_result("error", '[API Error: {"error":{"code":400,"message":'
                    '"The input exceeds the limit of 500 items",'
                    '"status":"INVALID_ARGUMENT"}}]'), False),
    (_gemini_result("error", "[API Error: Model gemini-x is unavailable in "
                    "region us-central1. Check the model name.]"), False),
    (_gemini_result("error", "[API Error: Invalid internal reference in the "
                    "tool schema.]"), False),
    (_gemini_result("error", "[API Error: You have exhausted your capacity on "
                    "this model.]"), True),
    (_gemini_result("error", "[API Error: Retry attempts exhausted]"), True),
    (_gemini_result("error", "[API Error: Resource exhausted. Please try "
                    "again later.]"), True),
    (_gemini_result("error", "[API Error: The context window is exhausted. "
                    "Start a new session.]"), False),
    (_gemini_result("error", "[API Error: RATE_LIMIT_EXCEEDED\nSuggested retry "
                    "after 60s.]", error_type="RetryableQuotaError"), True),
], ids=["quota_429", "server_503", "daily_quota", "deadline_exceeded",
        "tool_error", "invalid_argument", "quota_project_missing",
        "retry_log_then_success", "number_500_in_a_400_error",
        "unavailable_as_a_word", "internal_as_a_word", "capacity_exhausted",
        "retries_exhausted", "resource_exhausted", "context_window_exhausted",
        "quota_error_type"])
def test_gemini_model_api_error(result, is_infra):
    error = _bare(GeminiCliGenerator).model_api_error(result)
    assert bool(error) == is_infra


def _claude_result(*events):
    generator = _bare(ClaudeCodeGenerator)
    return generator, _result(1, "", generator._parse_stream_json(_stream(*events)))


def _claude_api_error(category, text):
    return {"type": "assistant", "error": category,
            "message": {"content": [{"type": "text", "text": text}]}}


@pytest.mark.parametrize("events, is_infra", [
    ([_claude_api_error("server_error", 'API Error: 529 {"type":"error","error":'
                        '{"type":"overloaded_error","message":"Overloaded"}}')],
     True),
    ([_claude_api_error("rate_limit", "API Error: Request rejected (429)")], True),
    ([_claude_api_error("authentication_failed", "Invalid API key")], False),
    ([_claude_api_error("unknown", "Request timed out")], True),
    ([_claude_api_error("unknown", "Unexpected end of JSON input")], False),
    ([{"type": "user", "message": {"content": [{
        "type": "tool_result", "tool_use_id": "t1", "is_error": True,
        "content": "cloud-sql__list_instances returned UNAVAILABLE"}]}}], False),
], ids=["overloaded_529", "rate_limit", "auth_failed", "api_timeout",
        "unknown_error", "tool_error_on_stdout"])
def test_claude_model_api_error(events, is_infra):
    generator, result = _claude_result(*events)
    assert bool(generator.model_api_error(result)) == is_infra


def test_claude_api_error_is_cleared_when_the_run_recovers():
    generator, result = _claude_result(
        _claude_api_error("rate_limit", "API Error: Request rejected (429)"),
        {"type": "assistant",
         "message": {"content": [{"type": "text", "text": "Done."}]}},
        {"type": "result", "subtype": "success", "is_error": False})
    assert generator.model_api_error(result) is None


def test_claude_subagent_message_does_not_clear_the_api_error():
    subagent_message = {
        "type": "assistant", "parent_tool_use_id": "toolu_task",
        "message": {"content": [{"type": "text", "text": "Subagent text."}]}}
    generator, result = _claude_result(
        subagent_message,
        _claude_api_error("rate_limit", "API Error: Request rejected (429)"),
        subagent_message)
    assert generator.model_api_error(result)


def test_claude_subagent_api_error_is_ignored():
    """The main agent gets a subagent API error as a tool result and continues."""
    subagent_error = _claude_api_error("rate_limit",
                                       "API Error: Request rejected (429)")
    subagent_error["parent_tool_use_id"] = "toolu_task"
    generator, result = _claude_result(
        subagent_error,
        {"type": "assistant", "parent_tool_use_id": None,
         "message": {"content": [{"type": "text", "text": "Done."}]}})
    assert generator.model_api_error(result) is None


def _codex_result(*events):
    generator = _bare(CodexCliGenerator)
    return generator, _result(1, "", generator._parse_stream_json(_stream(*events)))


@pytest.mark.parametrize("events, is_infra", [
    ([{"type": "error", "message": "stream disconnected - retrying sampling "
       "request (1/5)"},
      {"type": "turn.failed", "error": {"message": "exceeded retry limit, "
                                        "last status: 429 Too Many Requests"}}],
     True),
    ([{"type": "turn.failed", "error": {"message": "unexpected status 503 "
                                        "Service Unavailable"}}], True),
    ([{"type": "turn.failed", "error": {"message": "Quota exceeded. Check your "
                                        "plan and billing details."}}], True),
    ([{"type": "turn.failed", "error": {"message": "Selected model is at "
                                        "capacity. Please try a different "
                                        "model."}}], True),
    ([{"type": "turn.failed", "error": {"message": "We're currently "
                                        "experiencing high demand, which may "
                                        "cause temporary errors."}}], True),
    ([{"type": "error", "message": "stream disconnected - retrying sampling "
       "request (1/5)"},
      {"type": "turn.completed", "usage": {}}], False),
    ([{"type": "turn.failed", "error": {"message": "context window exceeded"}}],
     False),
    ([{"type": "turn.failed", "error": {"message": "stream disconnected before "
                                        "completion: Incomplete response "
                                        "returned, reason: content_filter"}}],
     False),
    ([{"type": "turn.failed", "error": {"message": "stream disconnected before "
                                        "completion: connection reset"}}],
     True),
    ([{"type": "turn.failed", "error": {"message": "unexpected status 400 Bad "
                                        "Request: array too long, expected at "
                                        "most 500 items"}}], False),
    ([{"type": "item.completed", "item": {
        "id": "item_1", "type": "mcp_tool_call", "server": "cloud-sql",
        "tool": "list_instances", "status": "failed",
        "error": {"message": "503 Service Unavailable: quota exceeded"}}},
      {"type": "turn.completed", "usage": {}}], False),
], ids=["retry_limit_429", "unexpected_503", "quota", "server_overloaded",
        "internal_server_error", "retry_then_success", "context_window",
        "content_filter", "stream_disconnected", "number_500_in_a_400_error",
        "mcp_tool_error"])
def test_codex_model_api_error(events, is_infra):
    generator, result = _codex_result(*events)
    assert bool(generator.model_api_error(result)) == is_infra


def test_default_model_api_error_ignores_stderr():
    """A gRPC proxy error_message comes from the remote agent and its tools."""
    result = _result(1, "Agent reported error: dataform__compile failed with "
                     "code 503 UNAVAILABLE")
    assert AgentCliGenerator.model_api_error(MagicMock(), result) is None


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
    generator.model_api_error.return_value = None
    patcher.start().return_value = generator

    def _run(cli_result, simulated_user_reply, model_generator=None,
             model_api_error=None):
        generator.safe_generate.return_value = cli_result
        generator.model_api_error.return_value = model_api_error
        simulated_user = MagicMock()
        simulated_user.model_generator = model_generator or MagicMock()
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
    error, simulated_user = run_scenario(
        _result(1), "next", model_api_error="429 Too Many Requests")
    assert error == "Agent infrastructure error (exit 1): 429 Too Many Requests"
    simulated_user.get_next_response.assert_not_called()


def test_timeout_is_not_an_infra_error(run_scenario):
    error, _ = run_scenario(_result(124), "TERMINATE",
                            model_api_error="503 Service Unavailable")
    assert error is None


def test_simulated_user_error_is_recorded(run_scenario):
    error, _ = run_scenario(_result(0), RuntimeError("model down"))
    assert error == "Simulated user error: model down"


@pytest.mark.parametrize("reply", ["", None], ids=["empty", "none"])
def test_empty_simulated_user_reply_is_recorded(run_scenario, reply):
    """QueryGenerator.generate returns "" after a simulated-user quota error.

    ClaudeGenerator returns None on any error.
    """
    error, simulated_user = run_scenario(_result(0), reply)
    assert error == ("Simulated user error: "
                     "empty reply from the simulated user model")
    assert simulated_user.get_next_response.call_count == 1


@pytest.mark.parametrize("noop_generator", [
    MagicMock(spec=NOOPGenerator), MagicMock(spec=NoopAgentGenerator),
], ids=["noop", "noop_agent"])
def test_empty_noop_simulated_user_reply_continues(run_scenario,
                                                   noop_generator):
    """The noop simulated user of proxy flows returns "" by design."""
    error, simulated_user = run_scenario(_result(0), "", noop_generator)
    assert error is None
    assert simulated_user.get_next_response.call_count == 2


def test_normal_run_has_no_error(run_scenario):
    error, _ = run_scenario(_result(0), "TERMINATE")
    assert error is None


def test_max_turns_is_not_an_error(run_scenario):
    """A conversation that reaches max_turns is scored as usual."""
    error, simulated_user = run_scenario(_result(0), "next")
    assert error is None
    # The scenario has 3 turns. No reply is requested after the last turn.
    assert simulated_user.get_next_response.call_count == 2


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


def _score_row(scenario_id, comparator, score, generated_error=None,
               comparison_error=None):
    return {"id": scenario_id, "comparator": comparator, "score": score,
            "generated_sql": "skipped", "generated_error": generated_error,
            "comparison_error": comparison_error}


def _summary(rows, orchestrator="agent"):
    """Returns the summary rows of `rows`, keyed by metric name."""
    config = {"orchestrator": orchestrator,
              "scorers": {row["comparator"]: {} for row in rows}}
    _, summary = analyze_result(rows, config)
    return summary.set_index("metric_name").to_dict(orient="index")


_UNSCORED = [
    {"generated_error": "Agent infrastructure error (exit 1): 429"},
    {"generated_error": "Simulated user error: model down"},
    {"comparison_error": RuntimeError("Invalid JSON in response")},
    # The orchestrator saves scores as JSON, so the error can be a string.
    {"comparison_error": "Invalid JSON in response"},
]
_UNSCORED_IDS = ["agent_error", "simulated_user_error", "scorer_error",
                 "scorer_error_as_text"]


@pytest.mark.parametrize("metric", ["goal_completion", "trajectory_matcher"])
@pytest.mark.parametrize("unscored", _UNSCORED, ids=_UNSCORED_IDS)
def test_pass_rate_skips_unscored_agent_trials(metric, unscored):
    """An unscored trial is not a pass and not a failure."""
    row = _summary([
        _score_row("passed", metric, 100.0),
        _score_row("failed", metric, 0.0),
        _score_row("unscored", metric, 0.0, **unscored),
    ])[metric]
    assert row["correct_results_count"] == 1
    assert row["total_results_count"] == 2


@pytest.mark.parametrize("metric", ["goal_completion", "turn_count"])
def test_metric_with_only_unscored_trials_has_no_results(metric):
    row = _summary([_score_row("s1", metric, 0.0,
                               generated_error="Simulated user error")])[metric]
    assert row["correct_results_count"] == 0
    assert row["total_results_count"] == 0


def test_average_skips_agent_trials_with_a_scorer_error():
    row = _summary([
        _score_row("s1", "turn_count", 4.0),
        _score_row("s2", "turn_count", 0.0,
                   comparison_error=RuntimeError("no stats")),
    ])["turn_count"]
    assert row["metric_score"] == 4.0


@pytest.mark.parametrize("unscored", _UNSCORED, ids=_UNSCORED_IDS)
def test_error_rows_still_fail_outside_agent_runs(unscored):
    """On the NL2SQL path, a generation or scorer error is a failure."""
    row = _summary([
        _score_row("passed", "exact_match", 100.0),
        _score_row("error", "exact_match", 0.0, **unscored),
    ], orchestrator="oneshot")["exact_match"]
    assert row["correct_results_count"] == 1
    assert row["total_results_count"] == 2


_PLAN = "List the Cloud SQL instances."
_GOOD_JUDGE_REPLY = json.dumps(
    {"sub_goals": [{"goal": "List the instances", "met": True}]})


@pytest.mark.parametrize("judge_reply, scenario", [
    (RuntimeError("judge model is down"), {"conversation_plan": _PLAN}),
    (TimeoutError("judge request timed out"), {"conversation_plan": _PLAN}),
    ("PASS, looks good", {"conversation_plan": _PLAN}),
    (json.dumps({"sub_goals": []}), {"conversation_plan": _PLAN}),
    (_GOOD_JUDGE_REPLY, {"conversation_plan": " "}),
], ids=["scorer_fails", "scorer_times_out", "judge_reply_malformed",
        "judge_reply_incomplete", "no_plan_and_no_criteria"])
def test_goal_completion_error_is_unscored(judge_reply, scenario):
    """The trial of a scorer error is not in the goal_completion pass rate."""
    config = {"orchestrator": "agent",
              "scorers": {"goal_completion": {"model_config": "fake_config"}}}
    judge = MagicMock(spec=["generate"])
    results = []
    with patch("scorers.goalcompletionrate.get_generator", return_value=judge):
        for scenario_id, reply, scenario_fields in [
                ("scored", _GOOD_JUDGE_REPLY, {"conversation_plan": _PLAN}),
                ("unscored", judge_reply, scenario)]:
            judge.generate.side_effect = (
                reply if isinstance(reply, Exception) else None)
            judge.generate.return_value = reply
            score_module.compare({
                "id": scenario_id, "nl_prompt": "", "golden_sql": "",
                "query_type": "", "golden_result": "", "golden_error": None,
                "generated_sql": "skipped", "generated_result": "",
                "generated_error": None, "dialects": [], "database": "",
                "job_id": "j1",
                "eval_results": json.dumps({"conversation_history": "[]",
                                            "scenario": scenario_fields}),
            }, config, results, global_models={})

    # The agent orchestrator saves the scores as JSON with `default=str`.
    results = json.loads(json.dumps(results, default=str))
    _, summary = analyze_result(results, config)
    row = summary.set_index("metric_name").loc["goal_completion"]
    assert row["correct_results_count"] == 1
    assert row["total_results_count"] == 1
