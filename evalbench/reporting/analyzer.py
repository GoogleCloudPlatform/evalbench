"""Analyze accuracy result from dataframe."""

import logging
import pandas as pd


def analyze_one_metric(
    df: pd.DataFrame,
    metric_name: str,
    metric_score: int,
    execution: bool = False,
    num_scorers: int = 1,
) -> dict:
    original_df_size = int(len(df) / num_scorers)

    if "generated_sql" not in df.columns:
        return {
            "metric_name": metric_name,
            "metric_score": metric_score,
            "correct_results_count": 0,
            "total_results_count": max(0, original_df_size),
        }

    df = df[df["generated_sql"].notna()]
    if execution:
        if "returned_sql" in df["comparator"].values:
            correct_results_count = len(
                df[
                    (df["generated_error"].isna())
                    & (df["comparator"] == "returned_sql")
                    & (df["score"] == 100)
                ]["id"].drop_duplicates()
            )
        else:
            correct_results_count = len(
                df[(df["generated_error"].isna())]["id"].drop_duplicates()
            )
    else:
        df = df[df["comparator"] == metric_name]
        non_binary_metrics = [
            "turn_count",
            "end_to_end_latency",
            "tool_call_latency",
            "token_consumption",
        ]
        if metric_name in non_binary_metrics:
            avg_val = df["score"].mean() if not df.empty else 0.0
            total_sum = df["score"].sum() if not df.empty else 0.0

            unit = ""
            if "latency" in metric_name:
                unit = " ms"
            elif "token" in metric_name:
                unit = " tokens"
            elif "turn" in metric_name:
                unit = " turns"

            logging.info(f"{metric_name}: \tAverage = {avg_val:.2f}{unit}")
            return {
                "metric_name": metric_name,
                "metric_score": avg_val,
                "correct_results_count": total_sum,
                "total_results_count": original_df_size,
            }

        correct_results_count = len(df[df["score"] == metric_score])
    if original_df_size > 0:
        pct = round(correct_results_count / original_df_size * 100, 2)
    else:
        pct = 0.0

    logging.info(
        f"{metric_name}: \t{correct_results_count}/{original_df_size} = {pct}%"
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
    if df.empty:
        return df, pd.DataFrame()

    scorers = experiment_config["scorers"]
    num_scorers = len(scorers)
    llm_metrics_list = [
        "goal_completion",
        "behavioral_metrics",
        "parameter_analysis",
    ]

    for metric_name in scorers:
        metric_name = metric_name.strip()
        metric_score = 100

        if metric_name in llm_metrics_list:
            metric_df = df[df["comparator"] == metric_name]
            for _, row in metric_df.iterrows():
                logging.info(f"\\n--- {metric_name} Analysis ---")
                if pd.notna(row.get("comparison_logs")):
                    logging.info(f"{row['comparison_logs']}")
                elif pd.notna(row.get("comparison_error")):
                    logging.info(f"Error: {row['comparison_error']}")
                else:
                    logging.info("No analysis provided.")
            continue

        summary = analyze_one_metric(
            df=df,
            metric_name=metric_name,
            metric_score=metric_score,
            num_scorers=num_scorers,
        )
        summary_scores.append(summary)

    summary = analyze_one_metric(
        df=df,
        metric_name="executable",
        metric_score=1,
        execution=True,
        num_scorers=num_scorers,
    )

    summary_scores.append(summary)
    summary_scores_df = pd.DataFrame.from_dict(summary_scores)

    existing_cols = [
        "generated_error",
        "comparator",
        "comparison_error",
        "generated_sql",
        "job_id",
        "id",
    ]
    # Filter to only existing columns before casting
    existing_cols = [col for col in existing_cols if col in df.columns]

    if existing_cols:
        df[existing_cols] = df[existing_cols].astype("string")

    return df, summary_scores_df
