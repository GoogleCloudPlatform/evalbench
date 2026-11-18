"""The suite stats in the summary of an agent run with num_trials above 1."""

import json
import os
import sys

import pandas as pd

from reporting import runstats
from reporting.analyzer import analyze_result
from reporting.report import Reporter, STORETYPE

_SUITE_COLUMNS = [
    "pass_rate", "interval_low", "interval_high", "flakiness", "confidence",
    "flaky_scenarios", "trial_min", "trial_median", "trial_max"]


def _row(scenario, trial, comparator, score, generated_error=None):
    """Returns a score row with the labels that a multi-trial agent run adds."""
    return {"id": f"{scenario}_trial_{trial}", "prompt_id": scenario,
            "trial_index": trial, "comparator": comparator, "score": score,
            "generated_sql": "skipped", "generated_error": generated_error,
            "comparison_error": None}


def _rows(scenario, comparator, scores):
    return [_row(scenario, trial, comparator, score)
            for trial, score in enumerate(scores)]


def _summary(rows, scorers=None):
    """Returns the summary rows of `rows`, keyed by metric name."""
    config = {"orchestrator": "agent",
              "scorers": scorers or {row["comparator"]: {} for row in rows}}
    scores_df, summary = analyze_result(rows, config)
    stats = runstats.compute_run_stats(scores_df, config, "job-1", "now")
    summary = runstats.add_suite_stats(summary, scores_df, stats, config)
    return summary.set_index("metric_name").to_dict(orient="index")


def test_pass_stats_of_the_suite():
    row = _summary(
        _rows("stable", "goal_completion", [100, 100, 100])
        + _rows("flaky", "goal_completion", [100, 0, 100])
        + _rows("once", "goal_completion", [100]))["goal_completion"]
    # 6 of the 7 trials of the suite passed.
    assert row["pass_rate"] == 0.8571
    assert (row["interval_low"], row["interval_high"]) == (0.4869, 0.9743)
    assert row["flaky_scenarios"] == 1
    # The average of the scenarios `stable` and `flaky`. The scenario `once`
    # has one trial, so it has no part in these two values.
    assert row["flakiness"] == 0.4444
    assert row["confidence"] == 0.1981


def test_unscored_trial_is_not_in_the_pass_rate_of_the_suite():
    error = "Agent infrastructure error (exit 1): 429"
    rows = _rows("s1", "goal_completion", [100, 100])
    rows += [_row("s2", 0, "goal_completion", 100.0),
             _row("s2", 1, "goal_completion", 0.0, generated_error=error)]
    row = _summary(rows)["goal_completion"]
    assert row["pass_rate"] == 1.0
    assert (row["interval_low"], row["interval_high"]) == (0.4385, 1.0)
    # The scenario s2 has one scored trial.
    assert row["flakiness"] == 0.0
    assert row["confidence"] == 0.271


def test_pass_threshold_applies_to_the_suite():
    rows = _rows("s1", "goal_completion", [80, 60])
    scorers = {"goal_completion": {"pass_threshold": 80}}
    assert _summary(rows, scorers)["goal_completion"]["pass_rate"] == 0.5


def test_numeric_metric_has_min_median_max_of_the_trial_averages():
    rows = _rows("s1", "goal_completion", [100, 100, 100])
    rows += _rows("s2", "goal_completion", [100, 100, 100])
    rows += _rows("s1", "turn_count", [1, 2, 6])
    rows += _rows("s2", "turn_count", [3, 4, 2])
    row = _summary(rows)["turn_count"]
    # The averages of the 3 trials are 2, 3, and 4.
    assert (row["trial_min"], row["trial_median"], row["trial_max"]) == (
        2.0, 3.0, 4.0)
    # The existing columns do not change.
    assert row["metric_score"] == 3.0
    assert row["total_results_count"] == 6


def test_trial_averages_use_the_scenarios_with_a_result_in_every_trial():
    error = "Agent infrastructure error (exit 1): 503"
    rows = _rows("full", "goal_completion", [100, 100])
    rows += _rows("full", "turn_count", [1, 3])
    # One trial only.
    rows += _rows("once", "goal_completion", [100])
    rows += _rows("once", "turn_count", [50])
    # One unscored trial.
    rows += [_row("error", 0, "goal_completion", 100.0),
             _row("error", 0, "turn_count", 70.0),
             _row("error", 1, "goal_completion", 0.0, generated_error=error),
             _row("error", 1, "turn_count", 0.0, generated_error=error)]
    row = _summary(rows)["turn_count"]
    assert (row["trial_min"], row["trial_median"], row["trial_max"]) == (
        1.0, 2.0, 3.0)


def test_each_row_has_only_its_own_stats():
    rows = _rows("s1", "goal_completion", [100, 0])
    rows += _rows("s1", "trajectory_matcher", [100, 100])
    rows += _rows("s1", "turn_count", [1, 3])
    summary = _summary(rows)
    for column in ("trial_min", "trial_median", "trial_max"):
        assert pd.isna(summary["goal_completion"][column])
    for column in ("pass_rate", "flakiness", "flaky_scenarios"):
        assert pd.isna(summary["turn_count"][column])
    for row in ("trajectory_matcher", "executable"):
        for column in _SUITE_COLUMNS:
            assert pd.isna(summary[row][column])


def test_run_without_goal_completion_has_no_pass_stats():
    summary = _summary(_rows("s1", "turn_count", [2, 4]))
    assert summary["turn_count"]["trial_max"] == 4.0
    for row in summary.values():
        assert pd.isna(row["pass_rate"])


def test_suite_with_no_scored_trial_has_empty_stats():
    error = "Simulated user error"
    rows = [_row("s1", trial, comparator, 0.0, generated_error=error)
            for trial in range(2)
            for comparator in ("goal_completion", "turn_count")]
    summary = _summary(rows)
    for row in summary.values():
        for column in _SUITE_COLUMNS:
            assert pd.isna(row[column])


class _Recorder(Reporter):
    def __init__(self):
        super().__init__({}, "job-1", None)
        self.summary = None

    def store(self, results, type):
        if type == STORETYPE.SUMMARY:
            self.summary = results


def _service_summary(tmp_path, scores):
    """Returns the summary that the gRPC service stores for `scores`."""
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
        [{"eval_id": row["id"], "prompt_id": "s1"} for row in scores]))
    scores_file = tmp_path / "scores.json"
    scores_file.write_text(json.dumps(scores))
    reporter = _Recorder()
    eval_service._process_results(
        [reporter], "job-1", "now", str(results_file), str(scores_file),
        None, config, {}, [])
    return reporter.summary


def test_summary_of_a_multi_trial_job_has_the_suite_stats(tmp_path):
    summary = _service_summary(
        tmp_path, _rows("s1", "goal_completion", [100, 0]))
    row = summary[summary["metric_name"] == "goal_completion"].iloc[0]
    assert row["pass_rate"] == 0.5
    assert row["job_id"] == "job-1"


def test_summary_of_a_job_with_one_trial_has_no_new_column(tmp_path):
    scores = [{"id": "s1", "comparator": "goal_completion", "score": 100.0}]
    summary = _service_summary(tmp_path, scores)
    assert list(summary.columns) == [
        "metric_name", "metric_score", "correct_results_count",
        "total_results_count", "job_id", "run_time"]
