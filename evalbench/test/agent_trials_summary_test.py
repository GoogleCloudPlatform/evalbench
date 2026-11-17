"""The summary of an agent run with num_trials above 1."""

from reporting.analyzer import analyze_result


def _row(scenario, trial, comparator, score, generated_error=None):
    """Returns a score row with the labels that a multi-trial agent run adds."""
    return {"id": f"{scenario}_trial_{trial}", "prompt_id": scenario,
            "trial_index": trial, "comparator": comparator, "score": score,
            "generated_sql": "skipped", "generated_error": generated_error,
            "comparison_error": None}


def _summary(rows, orchestrator="agent", **kwargs):
    """Returns the summary rows of `rows`, keyed by metric name."""
    config = {"orchestrator": orchestrator,
              "scorers": {row["comparator"]: {} for row in rows}}
    _, summary = analyze_result(rows, config, **kwargs)
    return summary.set_index("metric_name").to_dict(orient="index")


def test_numeric_metric_is_the_average_of_all_trials():
    row = _summary([
        _row("s1", 0, "turn_count", 2.0),
        _row("s1", 1, "turn_count", 4.0),
        _row("s2", 0, "turn_count", 3.0),
    ])["turn_count"]
    assert row["metric_score"] == 3.0
    assert row["total_results_count"] == 3


def test_pass_rate_counts_trials_not_scenarios():
    summary = _summary([
        _row("s1", 0, "trajectory_matcher", 100.0),
        _row("s1", 1, "trajectory_matcher", 0.0),
        _row("s1", 0, "goal_completion", 100.0),
        _row("s1", 1, "goal_completion", 50.0),
    ])
    assert summary["trajectory_matcher"]["correct_results_count"] == 1
    assert summary["trajectory_matcher"]["total_results_count"] == 2
    # goal_completion gives partial credit.
    assert summary["goal_completion"]["correct_results_count"] == 1.5
    assert summary["goal_completion"]["total_results_count"] == 2


def test_unscored_trial_is_not_in_the_pass_rate_or_the_average():
    error = "Agent infrastructure error (exit 1): 429"
    summary = _summary([
        _row("s1", 0, "goal_completion", 100.0),
        _row("s1", 1, "goal_completion", 0.0, generated_error=error),
        _row("s2", 0, "goal_completion", 100.0),
        _row("s2", 1, "goal_completion", 100.0),
        _row("s1", 0, "turn_count", 2.0),
        _row("s1", 1, "turn_count", 0.0, generated_error=error),
    ])
    assert summary["goal_completion"]["correct_results_count"] == 3
    assert summary["goal_completion"]["total_results_count"] == 3
    assert summary["turn_count"]["metric_score"] == 2.0


def test_executable_counts_each_trial_once():
    rows = [
        _row("s1", 0, "turn_count", 2.0),
        _row("s1", 1, "turn_count", 0.0, generated_error="Simulated user error"),
        _row("s2", 0, "turn_count", 1.0),
        _row("s2", 1, "turn_count", 1.0),
        _row("once", 0, "turn_count", 1.0),
    ]
    # evalbench.py passes the number of scenarios and the run's num_trials.
    row = _summary(rows, num_prompts=3, num_trials=2)["executable"]
    assert row["correct_results_count"] == 4
    assert row["total_results_count"] == 5


def test_nl2sql_trials_are_still_grouped_by_prompt():
    """On the NL2SQL path, a prompt passes only if every trial passes."""
    row = _summary([
        _row("p1", 0, "exact_match", 100.0),
        _row("p1", 1, "exact_match", 0.0),
        _row("p2", 0, "exact_match", 100.0),
        _row("p2", 1, "exact_match", 100.0),
    ], orchestrator="oneshot")["exact_match"]
    assert row["correct_results_count"] == 1
    assert row["total_results_count"] == 2
