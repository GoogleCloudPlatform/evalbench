"""Stats for each scenario of an agent run with more than one trial."""

import math
from typing import Any, Optional

import pandas as pd

from reporting.analyzer import (
    AGENT_ORCHESTRATORS,
    NUMERIC_METRICS,
    _is_sub_comparator,
    _resolve_metric_names_to_analyze,
    _scored_rows,
)

# The scorer that decides if a trial passes.
PASS_METRIC = "goal_completion"
DEFAULT_PASS_THRESHOLD = 100
# The confidence is the chance that the true pass rate is above this value.
CONFIDENCE_TARGET = 0.9
# The z value of a 95% interval.
WILSON_Z = 1.96
# The columns of the stats that hold a number of trials.
_COUNT_COLUMNS = (
    "num_trials", "scored_trials", "unscored_trials", "passed_trials")


def flakiness(passed: int, scored: int) -> float:
    """Returns 4p(1 - p) for the pass rate p.

    It is 0 if all trials agree and 1 at 50/50.
    """
    rate = passed / scored
    return 4 * rate * (1 - rate)


def confidence(passed: int, scored: int) -> float:
    """Returns the chance that the true pass rate is above CONFIDENCE_TARGET.

    It uses a uniform prior for the pass rate. The result is then the
    chance of at most `passed` successes in `scored + 1` attempts, each with
    a success chance of CONFIDENCE_TARGET.
    """
    attempts = scored + 1
    return sum(
        math.comb(attempts, i)
        * CONFIDENCE_TARGET ** i
        * (1 - CONFIDENCE_TARGET) ** (attempts - i)
        for i in range(passed + 1))


def wilson_interval(passed: int, scored: int) -> tuple[float, float]:
    """Returns the range that holds the true pass rate with 95% certainty."""
    rate = passed / scored
    z2 = WILSON_Z ** 2
    denominator = 1 + z2 / scored
    center = (rate + z2 / (2 * scored)) / denominator
    half = WILSON_Z * math.sqrt(
        rate * (1 - rate) / scored + z2 / (4 * scored ** 2)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _scorer_config(scorers: Any, metric: str) -> Any:
    """Returns the config of the scorer that reports its scores as `metric`."""
    if not isinstance(scorers, dict):
        return None
    if metric in scorers:
        return scorers[metric]
    # A named scorer can report its scores under `scorer_name`.
    for cfg in scorers.values():
        if isinstance(cfg, dict) and cfg.get("scorer_name") == metric:
            return cfg
    return None


def pass_threshold(scorers: Any, metric: str) -> float:
    """Returns the lowest score of `metric` that counts as a pass."""
    cfg = _scorer_config(scorers, metric)
    value = cfg.get("pass_threshold") if isinstance(cfg, dict) else None
    if value is None:
        return DEFAULT_PASS_THRESHOLD
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= 100):
        raise ValueError(
            f"pass_threshold of scorer {metric} must be a number from 0 "
            f"to 100, got {value!r}")
    return value


def check_pass_thresholds(scorers: Any) -> None:
    """Raises a ValueError if a scorer has an invalid `pass_threshold`."""
    for metric in (scorers if isinstance(scorers, dict) else {}):
        pass_threshold(scorers, metric)


def _trial_scores(df: pd.DataFrame, metric: str) -> pd.Series:
    """Returns the score of `metric` for each trial that has no error.

    A scorer with more than one row for a trial gets the lowest score.
    """
    rows = df[df["comparator"].astype(str).apply(
        lambda name: _is_sub_comparator(name, metric))]
    error_rows = rows.drop(_scored_rows(rows).index)
    rows = rows[~rows["trial_index"].isin(error_rows["trial_index"])]
    scores = pd.to_numeric(rows["score"], errors="coerce")
    return scores.groupby(rows["trial_index"]).min().dropna()


def _rounded(value: float) -> float:
    return round(float(value), 4)


def _scenario_stats(
    scenario_id: str, df: pd.DataFrame, metrics: list[str], scorers: Any
) -> dict:
    """Returns the stats row of one scenario."""
    trials = df["trial_index"].unique()
    row: dict[str, Any] = {
        "scenario_id": scenario_id,
        "num_trials": len(trials),
    }
    if PASS_METRIC in metrics:
        scores = _trial_scores(df, PASS_METRIC)
        scored_trials = set(scores.index)
    else:
        # Without the pass metric, a trial is unscored if it has an agent
        # error.
        scores = None
        agent_errors = df["generated_error"]
        failed = df[agent_errors.notna() & (agent_errors != "")]
        scored_trials = set(trials) - set(failed["trial_index"])
    scored = len(scored_trials)
    row["scored_trials"] = scored
    row["unscored_trials"] = len(trials) - scored

    stats: dict[str, Any] = dict.fromkeys((
        "passed_trials", "pass_rate", "flakiness", "flaky", "confidence",
        "interval_low", "interval_high", "score_mean", "score_std"))
    status = "ERROR" if scored < len(trials) else None
    if scores is not None and scored:
        passed = int((scores >= pass_threshold(scorers, PASS_METRIC)).sum())
        low, high = wilson_interval(passed, scored)
        stats.update({
            "passed_trials": passed,
            "pass_rate": _rounded(passed / scored),
            "flakiness": _rounded(flakiness(passed, scored)),
            "flaky": 0 < passed < scored,
            "confidence": _rounded(confidence(passed, scored)),
            "interval_low": _rounded(low),
            "interval_high": _rounded(high),
            "score_mean": _rounded(scores.mean()),
            # The sample standard deviation needs 2 scores or more.
            "score_std": _rounded(scores.std()) if scored > 1 else None,
        })
        if status is None:
            status = "PASS" if passed == scored else "FAIL"
    row.update(stats)
    row["status"] = status

    # The other metrics use the scored trials only.
    for metric in metrics:
        if metric == PASS_METRIC:
            continue
        values = _trial_scores(df, metric)
        values = values[values.index.isin(scored_trials)]
        if metric in NUMERIC_METRICS:
            for name in ("min", "median", "max"):
                row[f"{metric}_{name}"] = (
                    _rounded(getattr(values, name)())
                    if len(values) else None)
        else:
            threshold = pass_threshold(scorers, metric)
            row[f"{metric}_pass_rate"] = (
                _rounded((values >= threshold).mean())
                if len(values) else None)
    return row


def compute_run_stats(
    scores_df: pd.DataFrame, config: dict, job_id: str, run_time: Any
) -> Optional[pd.DataFrame]:
    """Returns one row of stats for each scenario of a multi-trial agent run.

    Returns None for a run with one trial and for a run that is not an agent
    run.
    """
    if config.get("orchestrator") not in AGENT_ORCHESTRATORS:
        return None
    if not {"trial_index", "prompt_id"} <= set(scores_df.columns):
        return None
    df = scores_df[
        scores_df["trial_index"].notna() & scores_df["prompt_id"].notna()]
    if df.empty:
        return None
    if "generated_error" not in df.columns:
        df = df.assign(generated_error=None)

    scorers = config.get("scorers") or {}
    comparators = df["comparator"].astype(str).unique()
    # Skip a scorer config key that has no score rows. Keep the pass metric:
    # without rows, its trials are unscored.
    metrics = [
        metric for metric in _resolve_metric_names_to_analyze(scorers, df)
        if metric == PASS_METRIC
        or any(_is_sub_comparator(name, metric) for name in comparators)]
    stats = pd.DataFrame([
        _scenario_stats(scenario_id, scenario_df, metrics, scorers)
        for scenario_id, scenario_df in df.groupby("prompt_id", sort=False)
    ])
    # Each column has one type in every job, also if it has no value. A
    # BigQuery table needs this.
    types = {"scenario_id": "string", "status": "string", "flaky": "boolean",
             **dict.fromkeys(_COUNT_COLUMNS, "Int64")}
    types.update(dict.fromkeys(stats.columns.difference(types), "float64"))
    stats = stats.astype(types)
    stats["job_id"] = job_id
    stats["run_time"] = run_time
    return stats
