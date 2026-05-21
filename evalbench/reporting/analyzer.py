"""Analyze accuracy result from dataframe."""

import logging
import pandas as pd


def analyze_one_metric(
    df: pd.DataFrame,
    metric_name: str,
    metric_score: int,
    execution: bool = False,
    num_scorers: int = 1,
    num_prompts: int = None,
    num_trials: int = None,
) -> dict:
    """Analyze one metric from dataframe with flexibility."""
    num_scorers = max(1, num_scorers)

    if num_prompts is not None:
        original_df_size = (
            num_prompts * num_trials
            if (execution and num_trials is not None)
            else num_prompts
        )
    elif "prompt_id" in df.columns and not df["prompt_id"].isna().all():
        original_df_size = len(df["prompt_id"].dropna().unique())
    else:
        original_df_size = int(len(df) / num_scorers)

    if execution:
        df_exec = df[df["generated_sql"].notna()]

        # Use prompt_id to count unique successful prompts if available
        if "prompt_id" in df_exec.columns and not df_exec["prompt_id"].isna().all():
            id_col = "prompt_id"
        else:
            id_col = "id"

        if "returned_sql" in df_exec["comparator"].values:
            correct_results_count = len(
                df_exec[
                    (df_exec["generated_error"].isna())
                    & (df_exec["comparator"] == "returned_sql")
                    & (df_exec["score"] == 100)
                ][id_col]
                .dropna()
                .drop_duplicates()
            )
        else:
            correct_results_count = len(
                df_exec[(df_exec["generated_error"].isna())][id_col]
                .dropna()
                .drop_duplicates()
            )
    else:
        df_metric = df[df["comparator"] == metric_name]

        if (
            "prompt_id" in df_metric.columns
            and not df_metric["prompt_id"].isna().all()
        ):
            # Aggregate at prompt level
            prompt_scores = df_metric.groupby("prompt_id")["score"].min()
            correct_results_count = len(
                prompt_scores[prompt_scores == metric_score])
            original_df_size = len(prompt_scores)

            if original_df_size == 0 and num_prompts is not None:
                original_df_size = num_prompts
        else:
            original_df_size = len(df_metric)
            if original_df_size == 0 and num_prompts is not None:
                original_df_size = num_prompts
            correct_results_count = len(
                df_metric[df_metric["score"] == metric_score])

            non_binary_metrics = [
                "turn_count",
                "end_to_end_latency",
                "tool_call_latency",
                "token_consumption",
            ]
            if metric_name in non_binary_metrics:
                avg_val = df_metric["score"].mean(
                ) if not df_metric.empty else 0.0
                total_sum = df_metric["score"].sum(
                ) if not df_metric.empty else 0.0

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

            correct_results_count = len(
                df_metric[df_metric["score"] == metric_score])

    percentage = (
        (correct_results_count / original_df_size * 100)
        if original_df_size > 0
        else 0.0
    )
    logging.info(
        f"{metric_name}: \t{correct_results_count}/{original_df_size} = "
        f"{round(percentage, 2)}%"
    )
    return {
        "metric_name": metric_name,
        "metric_score": metric_score,
        "correct_results_count": correct_results_count,
        "total_results_count": original_df_size,
    }


def analyze_result(
    scores,
    experiment_config: dict[str, str],
    num_prompts: int = None,
    num_trials: int = None,
):
    """Analyze accuracy result from dataframe."""
    summary_scores = []
    df = pd.DataFrame.from_dict(scores)

    # Ensure expected columns exist to avoid KeyErrors
    for col in ["generated_sql", "generated_error", "comparator", "score", "id"]:
        if col not in df.columns:
            df[col] = None

    # Adjust num_prompts to the count of unique evaluated items ONLY for agent/geminicli orchestrators where multiple scenarios are packed into one prompt.
    orchestrator = experiment_config.get("orchestrator", "") if isinstance(experiment_config, dict) else ""
    if orchestrator in ("agent", "geminicli"):
        if "id" in df.columns and not df["id"].isna().all():
            unique_ids = len(df["id"].dropna().unique())
            if num_prompts is None or unique_ids > num_prompts:
                num_prompts = unique_ids

    scorers = experiment_config["scorers"]
    num_scorers = len(scorers)
    llm_metrics_list = [
        "goal_completion",
        "behavioral_metrics",
        "parameter_analysis",
        "skills_best_practices",
    ]

    for metric_name in scorers:
        metric_name = metric_name.strip()
        metric_score = 100

        if metric_name in llm_metrics_list:
            metric_df = df[df["comparator"] == metric_name]
            for _, row in metric_df.iterrows():
                logging.info(f"\n--- {metric_name} Analysis ---")
                if pd.notna(row.get("comparison_logs")):
                    logging.info(f"{row['comparison_logs']}")
                elif pd.notna(row.get("comparison_error")):
                    logging.info(f"Error: {row['comparison_error']}")
                else:
                    logging.info("No analysis provided.")
            if metric_name not in ["goal_completion", "skills_best_practices"]:
                continue

        summary = analyze_one_metric(
            df=df,
            metric_name=metric_name,
            metric_score=metric_score,
            num_scorers=num_scorers,
            num_prompts=num_prompts,
            num_trials=num_trials,
        )
        summary_scores.append(summary)

    summary = analyze_one_metric(
        df=df,
        metric_name="executable",
        metric_score=1,
        execution=True,
        num_scorers=num_scorers,
        num_prompts=num_prompts,
        num_trials=num_trials,
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
