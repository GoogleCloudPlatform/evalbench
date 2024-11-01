"""Analyze accuracy result from dataframe."""

import logging
import pandas as pd
from tabulate import tabulate


def analyze_one_metric(
    df: pd.DataFrame,
    metric_name: str,
    metric_score: int,
    execution: bool = False,
    num_scorers: int = 1,
) -> int:
    """Analyze one metric from dataframe with flexibility."""
    original_df_size = int(len(df) / num_scorers)
    df = df[df["generated_sql"].notna()]
    if execution:
        if 'returned_sql' in df['comparator'].values:
            correct_results_count = len(
                df[
                    (df["generated_error"].isna())
                    & (df["comparator"] == "returned_sql")
                    & (df["score"] == 100)
                ]["id"].drop_duplicates()
            )
        else:
            correct_results_count = len(
                df[
                    (df["generated_error"].isna())
                ]["id"].drop_duplicates()
            )
    else:
        df = df[df["comparator"] == metric_name]
        correct_results_count = len(df[df["score"] == metric_score])
    logging.info(
        f"{metric_name}: \t{correct_results_count}/{original_df_size} = "
        f"{round(correct_results_count/original_df_size * 100, 2)}%"
    )
    return {
        "metric_name": metric_name,
        "metric_score": metric_score,
        "correct_results_count": correct_results_count,
        "total_results_count": original_df_size,
    }


def analyze_result(scores, experiment_config: dict[str, str]):
    """Analyze accuracy result from dataframe."""
    summary_scores = []
    df = pd.DataFrame.from_dict(scores)
    scorers = experiment_config["scorers"]
    num_scorers = len(scorers)
    for metric_name in scorers:
        metric_name = metric_name.strip()
        if metric_name == "recall_match":
            metric_score = 1
        else:
            metric_score = 100
        summary = analyze_one_metric(
            df=df,
            metric_name=metric_name,
            metric_score=metric_score,
            num_scorers=num_scorers,
        )
        summary_scores.append(summary)

    summary = analyze_one_metric(
        df=df, metric_name="executable", metric_score=1, execution=True, num_scorers=num_scorers
    )

    summary_scores.append(summary)
    summary_scores_df = pd.DataFrame.from_dict(summary_scores)
    df[["generated_error", "comparator", "comparison_error", "generated_sql", "job_id"]] = df[
        ["generated_error", "comparator", "comparison_error", "generated_sql", "job_id"]
    ].astype("string")
    df[["id"]] = df[["id"]].astype("int64")
    return df, summary_scores_df
