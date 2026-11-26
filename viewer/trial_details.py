"""The result of each trial of a scenario, for the Dashboard tab.

A multi-trial agent run labels each row of scores.csv with `prompt_id`, which
is the scenario id, and with `trial_index`. This module reads the
goal_completion row and the errors of each trial. It has no Mesop code.
"""

import logging
import os
from typing import Any, Optional

import pandas as pd

SCORES_FILE = "scores.csv"
# The scorer that decides if a trial passes.
PASS_METRIC = "goal_completion"
# scores.csv has no trial results without these columns.
_REQUIRED_COLUMNS = {"id", "prompt_id", "trial_index", "comparator"}
# The goal_completion scorer starts the line of each goal with one of these
# tags. The value tells if the goal is met.
_GOAL_TAGS = {"[MET] ": True, "[UNMET] ": False}
_SUMMARY_TAG = "Summary: "
# The scorer puts this text between a goal and the reasoning of the judge.
_REASONING_SEPARATOR = " — "


def _text(value: Any) -> str:
    """Returns a cell as text, or an empty string for an empty cell."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip()


def _number(value: Any) -> Optional[float]:
    """Returns a cell as a float, or None if it is empty or not a number."""
    number = pd.to_numeric(_text(value), errors="coerce")
    return None if pd.isna(number) else float(number)


def parse_judge_log(log: Any) -> tuple[list[dict], str]:
    """Returns the goals and the summary from a goal_completion log.

    The scorer writes one line for each success criterion or sub-goal:
    `[MET] <goal> — <reasoning>` or `[UNMET] <goal> — <reasoning>`. The last
    line can be `Summary: <text>`. A line without a tag continues the line
    before it. The first line, with the count of met goals, is not used.

    Each returned goal has the keys `met`, `goal`, and `reasoning`.
    """
    goals: list[dict] = []
    summary = ""
    in_summary = False
    for line in _text(log).splitlines():
        tag = next((t for t in _GOAL_TAGS if line.startswith(t)), None)
        if tag:
            goal, _, reasoning = line[len(tag):].partition(
                _REASONING_SEPARATOR)
            goals.append({"met": _GOAL_TAGS[tag], "goal": goal.strip(),
                          "reasoning": reasoning.strip()})
            in_summary = False
        elif line.startswith(_SUMMARY_TAG):
            summary = line[len(_SUMMARY_TAG):].strip()
            in_summary = True
        elif in_summary:
            summary = f"{summary} {line.strip()}".strip()
        elif goals:
            goals[-1]["reasoning"] = (
                f"{goals[-1]['reasoning']} {line.strip()}".strip())
    return goals, summary


def _trial(eval_rows: pd.DataFrame, trial_index: int) -> dict:
    """Returns the result of one trial from its rows of scores.csv."""
    goal_rows = eval_rows[eval_rows["comparator"] == PASS_METRIC]
    goal_row = goal_rows.iloc[0] if len(goal_rows) else {}
    goals, summary = parse_judge_log(goal_row.get("comparison_logs"))
    # Each row of a trial has the same agent error.
    agent_errors = [
        _text(value) for value in eval_rows.get("generated_error", [])]
    return {
        "eval_id": _text(eval_rows["id"].iloc[0]),
        "trial_index": trial_index,
        "score": _number(goal_row.get("score")),
        "goals": goals,
        "summary": summary,
        "agent_error": next((e for e in agent_errors if e), ""),
        "scorer_error": _text(goal_row.get("comparison_error")),
    }


def load_trials(results_dir: str) -> dict[str, list[dict]]:
    """Returns the trials of each scenario from scores.csv, by scenario id.

    The trials of a scenario are in the order of `trial_index`. Each trial
    has these keys:

    - `eval_id`: the id of the trial, `<scenario>_trial_<k>`.
    - `trial_index`: the number of the trial. The first trial is 0.
    - `score`: the goal_completion score, or None if the trial has no score.
    - `goals`, `summary`: the result of `parse_judge_log`.
    - `agent_error`, `scorer_error`: the error text, or an empty string.

    The result is empty for a run without trial labels in scores.csv.
    """
    path = os.path.join(results_dir, SCORES_FILE)
    if not os.path.exists(path):
        return {}
    try:
        scores = pd.read_csv(path)
    except Exception as e:
        logging.warning("Could not read %s: %s", path, e)
        return {}
    if not _REQUIRED_COLUMNS <= set(scores.columns):
        return {}

    scores = scores.assign(
        trial_index=pd.to_numeric(scores["trial_index"], errors="coerce"))
    scores = scores[
        scores["prompt_id"].notna() & scores["trial_index"].notna()]
    trials: dict[str, list[dict]] = {}
    for (scenario_id, trial_index), eval_rows in scores.groupby(
            ["prompt_id", "trial_index"], sort=True):
        trials.setdefault(str(scenario_id), []).append(
            _trial(eval_rows, int(trial_index)))
    return trials
