"""goal_completion rollup for the viewer fallbacks, used when summary.csv has
no goal_completion row."""

import pandas as pd


def goal_completion_scores(scores: pd.DataFrame) -> pd.Series:
    """Returns one goal_completion score per scenario from scores.csv rows.

    Matches the analyzer: with `prompt_id`, each prompt counts its lowest
    numeric trial. A scenario with no numeric score counts as 0.
    """
    rows = scores[scores["comparator"] == "goal_completion"]
    values = pd.to_numeric(rows["score"], errors="coerce")
    if "prompt_id" in rows.columns and rows["prompt_id"].notna().any():
        values = values.groupby(rows["prompt_id"]).min()
    return values.fillna(0)
