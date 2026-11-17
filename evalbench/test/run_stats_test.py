"""The scenario stats of an agent run with num_trials above 1."""

import json
import os
import sys

import pandas as pd
import pytest

from evalbench.evalbench import _store_reports
from evaluator.agentorchestrator import AgentOrchestrator
from reporting import runstats
from reporting.analyzer import analyze_result
from reporting.csv import CsvReporter
from reporting.report import Reporter


def _row(scenario, trial, comparator, score, generated_error=None,
         comparison_error=None):
    """Returns a score row with the labels that a multi-trial agent run adds."""
    return {"id": f"{scenario}_trial_{trial}", "prompt_id": scenario,
            "trial_index": trial, "comparator": comparator, "score": score,
            "generated_sql": "skipped", "generated_error": generated_error,
            "comparison_error": comparison_error}


def _goal_rows(scenario, scores):
    return [_row(scenario, trial, "goal_completion", score)
            for trial, score in enumerate(scores)]


def _stats(rows, scorers=None, orchestrator="agent"):
    """Returns the stats rows of `rows`, keyed by scenario id."""
    config = {"orchestrator": orchestrator,
              "scorers": scorers or {row["comparator"]: {} for row in rows}}
    # The job computes the stats from the frame that the analyzer returns.
    scores_df, _ = analyze_result(rows, config)
    stats = runstats.compute_run_stats(scores_df, config, "job-1", "now")
    if stats is None:
        return None
    return stats.set_index("scenario_id").to_dict(orient="index")


@pytest.mark.parametrize("passed, scored, expected", [
    (1, 1, 0.19), (3, 3, 0.34), (5, 5, 0.47), (11, 11, 0.72),
    (15, 15, 0.81), (2, 3, 0.05), (0, 3, 0.0),
])
def test_confidence(passed, scored, expected):
    assert round(runstats.confidence(passed, scored), 2) == expected


@pytest.mark.parametrize("passed, scored, expected", [
    (3, 3, (0.44, 1.0)), (2, 3, (0.21, 0.94)), (0, 3, (0.0, 0.56)),
    (15, 15, (0.80, 1.0)),
])
def test_wilson_interval(passed, scored, expected):
    low, high = runstats.wilson_interval(passed, scored)
    assert (round(low, 2), round(high, 2)) == expected


@pytest.mark.parametrize("passed, scored, expected", [
    (3, 3, 0.0), (0, 3, 0.0), (1, 2, 1.0), (2, 3, 0.8889),
])
def test_flakiness(passed, scored, expected):
    assert round(runstats.flakiness(passed, scored), 4) == expected


def test_stable_pass_stable_failure_and_flaky_scenario():
    stats = _stats(
        _goal_rows("pass", [100, 100, 100])
        + _goal_rows("fail", [0, 50, 0])
        + _goal_rows("flaky", [100, 0, 100]))

    assert stats["pass"]["status"] == "PASS"
    assert stats["pass"]["pass_rate"] == 1.0
    assert stats["pass"]["flaky"] is False
    assert stats["pass"]["confidence"] == 0.3439
    assert stats["pass"]["interval_low"] == 0.4385
    assert stats["pass"]["interval_high"] == 1.0

    assert stats["fail"]["status"] == "FAIL"
    assert stats["fail"]["passed_trials"] == 0
    assert stats["fail"]["flakiness"] == 0.0
    assert stats["fail"]["flaky"] is False

    assert stats["flaky"]["status"] == "FAIL"
    assert stats["flaky"]["passed_trials"] == 2
    assert stats["flaky"]["pass_rate"] == 0.6667
    assert stats["flaky"]["flakiness"] == 0.8889
    assert stats["flaky"]["flaky"] is True


def test_score_mean_and_std_show_partial_credit():
    row = _stats(_goal_rows("s1", [80, 80, 60]))["s1"]
    assert row["pass_rate"] == 0.0
    assert row["flakiness"] == 0.0
    assert row["score_mean"] == 73.3333
    assert row["score_std"] == 11.547


def test_pass_threshold_lowers_the_score_that_passes():
    rows = _goal_rows("s1", [80, 80, 60])
    row = _stats(rows, {"goal_completion": {"pass_threshold": 80}})["s1"]
    assert row["passed_trials"] == 2
    assert row["status"] == "FAIL"


@pytest.mark.parametrize("errors", [
    {"generated_error": "Agent infrastructure error (exit 1): 429"},
    {"comparison_error": "The judge reply is malformed."},
])
def test_unscored_trial_is_not_in_the_pass_rate(errors):
    rows = _goal_rows("s1", [100, 100])
    rows.append(_row("s1", 2, "goal_completion", 0.0, **errors))
    row = _stats(rows)["s1"]
    assert row["num_trials"] == 3
    assert row["scored_trials"] == 2
    assert row["unscored_trials"] == 1
    assert row["pass_rate"] == 1.0
    assert row["flaky"] is False
    # One unscored trial is not enough data to judge the scenario.
    assert row["status"] == "ERROR"


def test_scenario_with_no_scored_trial_has_empty_stats():
    error = "Simulated user error"
    rows = [_row("s1", trial, comparator, 0.0, generated_error=error)
            for trial in range(2)
            for comparator in ("goal_completion", "turn_count")]
    row = _stats(rows + _goal_rows("s2", [100, 100]))["s1"]
    assert row["status"] == "ERROR"
    assert row["scored_trials"] == 0
    for column in ("passed_trials", "pass_rate", "flakiness", "flaky",
                   "confidence", "interval_low", "score_mean",
                   "turn_count_median"):
        assert pd.isna(row[column]), column


def test_numeric_metric_has_min_median_max_of_the_scored_trials():
    rows = _goal_rows("s1", [100, 100, 100])
    rows += [_row("s1", trial, "token_consumption", tokens)
             for trial, tokens in enumerate([300, 100, 200])]
    # Trial 3 has an agent error. Its token count is not a result.
    error = "Agent infrastructure error (exit 1): 503"
    rows += [_row("s1", 3, "goal_completion", 0.0, generated_error=error),
             _row("s1", 3, "token_consumption", 5.0, generated_error=error)]
    row = _stats(rows)["s1"]
    assert row["token_consumption_min"] == 100
    assert row["token_consumption_median"] == 200
    assert row["token_consumption_max"] == 300


def test_other_scorer_has_a_pass_rate():
    rows = _goal_rows("s1", [100, 100, 100])
    rows += [_row("s1", trial, "trajectory_matcher", score)
             for trial, score in enumerate([100, 50, 100])]
    row = _stats(rows)["s1"]
    assert row["trajectory_matcher_pass_rate"] == 0.6667

    scorers = {"goal_completion": {},
               "trajectory_matcher": {"pass_threshold": 50}}
    assert _stats(rows, scorers)["s1"]["trajectory_matcher_pass_rate"] == 1.0


def test_named_scorer_uses_its_own_pass_threshold():
    rows = _goal_rows("s1", [100, 100])
    rows += [_row("s1", trial, "tools", score)
             for trial, score in enumerate([100, 50])]
    scorers = {"goal_completion": {},
               "my_matcher": {"type": "trajectory_matcher",
                              "scorer_name": "tools", "pass_threshold": 50}}
    row = _stats(rows, scorers)["s1"]
    assert row["tools_pass_rate"] == 1.0
    # The config key has no score rows, so it gets no column.
    assert "my_matcher_pass_rate" not in row


def test_scenario_that_runs_in_one_trial():
    row = _stats(
        _goal_rows("many", [100, 100]) + _goal_rows("once", [100]))["once"]
    assert row["num_trials"] == 1
    assert row["pass_rate"] == 1.0
    assert row["confidence"] == 0.19
    assert pd.isna(row["score_std"])
    assert row["status"] == "PASS"


def test_run_without_goal_completion_has_no_pass_stats():
    rows = [_row("s1", 0, "turn_count", 2.0),
            _row("s1", 1, "turn_count", 4.0),
            _row("s1", 2, "turn_count", 0.0, generated_error="429")]
    row = _stats(rows)["s1"]
    assert row["scored_trials"] == 2
    assert row["turn_count_max"] == 4
    assert pd.isna(row["pass_rate"])
    assert row["status"] == "ERROR"


def test_no_stats_for_a_run_with_one_trial():
    rows = [{"id": "s1", "comparator": "goal_completion", "score": 100.0}]
    assert _stats(rows) is None


def test_no_stats_for_an_nl2sql_run():
    rows = [_row("p1", 0, "exact_match", 100.0),
            _row("p1", 1, "exact_match", 0.0)]
    assert _stats(rows, orchestrator="oneshot") is None


def test_stats_rows_have_the_job_labels():
    config = {"orchestrator": "agent", "scorers": {"goal_completion": {}}}
    scores_df, _ = analyze_result(_goal_rows("s1", [100, 100]), config)
    stats = runstats.compute_run_stats(scores_df, config, "job-1", "now")
    assert list(stats["job_id"]) == ["job-1"]
    assert list(stats["run_time"]) == ["now"]


@pytest.mark.parametrize("value", [-1, 101, "80", True, [80]])
def test_invalid_pass_threshold_stops_the_job_at_the_start(value):
    config = {"scorers": {"goal_completion": {"pass_threshold": value}}}
    with pytest.raises(ValueError, match="pass_threshold of scorer"):
        AgentOrchestrator(config, None, None)


@pytest.mark.parametrize("value", [0, 80, 99.5, 100])
def test_valid_pass_threshold(value):
    scorers = {"goal_completion": {"pass_threshold": value}}
    assert runstats.pass_threshold(scorers, "goal_completion") == value
    assert runstats.pass_threshold(scorers, "trajectory_matcher") == 100


def test_csv_reporter_writes_run_stats(tmp_path):
    reporter = CsvReporter({"output_directory": str(tmp_path)}, "job-1", None)
    reporter.store_run_stats(pd.DataFrame([{"scenario_id": "s1"}]))
    stored = pd.read_csv(tmp_path / "job-1" / "run_stats.csv")
    assert list(stored["scenario_id"]) == ["s1"]


class _OtherReporter(Reporter):
    def store(self, results, type):
        raise AssertionError("store must not get the run stats")


def test_other_reporters_ignore_run_stats():
    _OtherReporter({}, "job-1", None).store_run_stats(pd.DataFrame())


class _Recorder(Reporter):
    def __init__(self):
        super().__init__({}, "job-1", None)
        self.run_stats = []

    def store(self, results, type):
        pass

    def store_run_stats(self, run_stats):
        self.run_stats.append(run_stats)


def test_store_reports_stores_run_stats_only_if_the_job_has_them():
    frame = pd.DataFrame()
    stats = pd.DataFrame([{"scenario_id": "s1"}])

    one_trial = _Recorder()
    _store_reports([one_trial], frame, frame, frame, frame)
    assert one_trial.run_stats == []

    multi_trial = _Recorder()
    _store_reports([multi_trial], frame, frame, frame, frame, stats)
    assert multi_trial.run_stats == [stats]


def test_grpc_service_stores_run_stats(tmp_path):
    # The generated gRPC code imports the proto modules by their bare names.
    proto_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "evalproto")
    if proto_dir not in sys.path:
        sys.path.insert(0, proto_dir)
    import eval_service

    config = {"orchestrator": "agent", "scorers": {"goal_completion": {}}}
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(
        [{"eval_id": f"s1_trial_{trial}", "prompt_id": "s1"}
         for trial in range(2)]))
    scores_file = tmp_path / "scores.json"
    scores_file.write_text(json.dumps(_goal_rows("s1", [100, 0])))

    reporter = _Recorder()
    eval_service._process_results(
        [reporter], "job-1", "now", str(results_file), str(scores_file),
        None, config, {}, [])

    assert len(reporter.run_stats) == 1
    assert list(reporter.run_stats[0]["pass_rate"]) == [0.5]
