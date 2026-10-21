"""Analyze accuracy result from dataframe."""

import logging
import pandas as pd


def _is_sub_comparator(comp: str, metric_name: str) -> bool:
    if comp == metric_name:
        return True
    if comp.startswith(f"{metric_name}_"):
        suffix = comp[len(metric_name) + 1:]
        return suffix.isdigit()
    return False


def _extract_base_metric_name(comp: str) -> str:
    """Extract base metric name from sub-comparator (e.g. 'rubric_0')."""
    if "_" in comp and comp.rsplit("_", 1)[1].isdigit():
        return comp.rsplit("_", 1)[0]
    return comp


def _resolve_metric_names_to_analyze(
    scorers: dict, df: pd.DataFrame
) -> list[str]:
    """Determine top-level metric names to analyze."""
    configured_scorers = [
        k.strip()
        for k in (scorers.keys() if isinstance(scorers, dict) else [])
        if k and k.strip() != "executable"
    ]

    metric_names = []

    # 1. Always include configured scorers so zero-result scorers are kept
    for key in configured_scorers:
        if key not in metric_names:
            metric_names.append(key)

    # 2. Add unconfigured comparators present in the DataFrame
    if "comparator" in df.columns:
        raw_comparators = [
            str(comp).strip() for comp in df["comparator"].dropna().unique()
            if comp and str(comp).strip() != "executable"
        ]
        for comp in raw_comparators:
            has_sub = any(
                _is_sub_comparator(comp, metric) for metric in metric_names
            )
            if not has_sub:
                base_name = _extract_base_metric_name(comp)
                if base_name not in metric_names:
                    metric_names.append(base_name)

    return metric_names


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
        df_exec = (
            df[df["generated_sql"].notna()]
            if "generated_sql" in df.columns
            else pd.DataFrame()
        )

        # Use prompt_id to count unique successful prompts if available
        has_prompt_id = (
            "prompt_id" in df_exec.columns
            and not df_exec["prompt_id"].isna().all()
        )
        if has_prompt_id:
            id_col = "prompt_id"
        else:
            id_col = "id"

        if df_exec.empty or id_col not in df_exec.columns:
            correct_results_count = 0
        elif (
            "comparator" in df_exec.columns
            and "returned_sql" in df_exec["comparator"].values
        ):
            has_score = "score" in df_exec.columns
            score_filter = (df_exec["score"] == 100) if has_score else False
            has_error = "generated_error" in df_exec.columns
            error_filter = (
                (df_exec["generated_error"].isna())
                | (df_exec["generated_error"] == "")
            ) if has_error else True
            correct_results_count = len(
                df_exec[
                    error_filter
                    & (df_exec["comparator"] == "returned_sql")
                    & score_filter
                ][id_col]
                .dropna()
                .drop_duplicates()
            )
        else:
            has_error = "generated_error" in df_exec.columns
            error_filter = (
                (df_exec["generated_error"].isna())
                | (df_exec["generated_error"] == "")
            ) if has_error else True
            correct_results_count = len(
                df_exec[error_filter][id_col]
                .dropna()
                .drop_duplicates()
            )
    else:
        df_metric = (
            df[
                df["comparator"].astype(str).apply(
                    lambda c: _is_sub_comparator(c, metric_name)
                )
            ]
            if "comparator" in df.columns
            else pd.DataFrame()
        )

        if df_metric.empty or "score" not in df_metric.columns:
            correct_results_count = 0
            if original_df_size == 0 and num_prompts is not None:
                original_df_size = num_prompts
            elif original_df_size == 0:
                original_df_size = len(df_metric)
        elif (
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
                "agent_steps",
                "end_to_end_latency",
                "tool_call_latency",
                "token_consumption",
                "tokens_processed",
                "effective_billed_tokens",
                # 0-100 grades: matching against 100 would report every
                # grade below it as a flat 0%.
                "dataset_quality",
                "tool_activation_faithfulness",
                "discoverability_coverage",
                "error_recovery_coverage",
                "composition_coverage",
                "cuj_diversity",
                "skills_best_practices",
            ]
            if metric_name in non_binary_metrics:
                avg_val = (
                    df_metric["score"].mean()
                    if (not df_metric.empty and "score" in df_metric.columns)
                    else 0.0
                )
                total_sum = (
                    df_metric["score"].sum()
                    if (not df_metric.empty and "score" in df_metric.columns)
                    else 0.0
                )

                unit = ""
                if "latency" in metric_name:
                    unit = " ms"
                elif "token" in metric_name:
                    unit = " tokens"
                elif "turn" in metric_name:
                    unit = " turns"
                elif "agent_steps" in metric_name:
                    unit = " steps"

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
    cols = ["generated_sql", "generated_error", "comparator", "score", "id"]
    for col in cols:
        if col not in df.columns:
            df[col] = None

    # Adjust num_prompts for agent/geminicli orchestrators
    orch = (
        experiment_config.get("orchestrator", "")
        if isinstance(experiment_config, dict)
        else ""
    )
    if orch in ("agent", "geminicli"):
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

    metric_names_to_analyze = _resolve_metric_names_to_analyze(scorers, df)

    for metric_name in metric_names_to_analyze:
        metric_name = metric_name.strip()
        metric_score = 100

        if metric_name in llm_metrics_list:
            metric_df = df[df["comparator"] == metric_name]
            for _, row in metric_df.iterrows():
                row_id = row.get("id")
                header = f"--- {metric_name} Analysis"
                if pd.notna(row_id):
                    header += f" [id={row_id}]"
                header += " ---"
                logging.info(f"\n{header}")
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


# Longest ``comparison_logs`` (the judge's reasoning) kept per scenario entry
# in the response summary. Keeps a run with many LLM-scored scenarios well
# under the gRPC message limits while preserving the explanation of a failure.
SCENARIO_LOG_MAX_CHARS = 4000

# Binary scorers report 100 for a pass (see ``metric_score`` in
# ``analyze_result``); anything else is a failure or a non-binary measurement.
_FULL_MARKS = 100


def _json_scalar(value):
    """Return ``value`` as a plain Python scalar, or None when it is missing."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        # Not a scalar (e.g. a list); pass it through unchanged.
        return value
    if hasattr(value, "item"):
        # numpy scalar -> native Python type, so json.dumps accepts it.
        return value.item()
    return value


def scenario_scores(
    scores_df: pd.DataFrame, max_log_chars: int = SCENARIO_LOG_MAX_CHARS
) -> list[dict]:
    """Per-scenario scorer outcomes for the response summary.

    Returns one JSON-serialisable dict per ``(id, comparator)`` row of the
    scores dataframe, in dataframe order. Every entry carries ``id``,
    ``comparator`` and ``score``; ``comparison_error`` and ``generated_error``
    are added when set. ``comparison_logs`` is added, truncated to
    ``max_log_chars``, only for rows that did not get full marks, so a clean
    run stays small while every failure keeps the judge's reasoning.

    Rows without a scenario ``id`` are skipped.
    """
    if scores_df is None or scores_df.empty or "id" not in scores_df.columns:
        return []

    entries = []
    for _, row in scores_df.iterrows():
        scenario_id = _json_scalar(row.get("id"))
        if scenario_id is None or scenario_id == "":
            continue
        score = _json_scalar(row.get("score"))
        if isinstance(score, float) and score.is_integer():
            # pandas upcasts a column mixing binary (0/100) and measured
            # (e.g. latency) scores to float; keep whole numbers as ints.
            score = int(score)
        entry = {
            "id": str(scenario_id),
            "comparator": _json_scalar(row.get("comparator")),
            "score": score,
        }
        for column in ("comparison_error", "generated_error"):
            text = _json_scalar(row.get(column))
            if text is not None and text != "":
                entry[column] = str(text)
        logs = _json_scalar(row.get("comparison_logs"))
        if logs is not None and logs != "" and score != _FULL_MARKS:
            logs = str(logs)
            if len(logs) > max_log_chars:
                dropped = len(logs) - max_log_chars
                logs = f"{logs[:max_log_chars]}... [truncated {dropped} chars]"
            entry["comparison_logs"] = logs
        entries.append(entry)
    return entries
