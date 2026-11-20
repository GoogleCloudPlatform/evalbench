import inspect
import logging
import os
from typing import Any

from dataset.evaloutput import EvalOutput
from scorers import agentsteps
from scorers import analyticsscorer
from scorers import behavioralmetrics
from scorers import binaryrubricscorer
from scorers import comparator
from scorers import dataformcloudscorer
from scorers import dataformscorer
from scorers import dbtscorer
from scorers import effectivebilledtokens
from scorers import endtoendlatency
from scorers import executablesql
from scorers import exactmatcher
from scorers import generatedqueryregexpmatcher
from scorers import goalcompletionrate
from scorers import llmrater
from scorers import namedscorer
from scorers import parameteranalysis
from scorers import pythonscorer
from scorers import recallmatcher
from scorers import returnedsql
from scorers import setmatcher
from scorers import skillsbestpractices
from scorers import skillstrajectorymatcher
from scorers import tokenconsumption
from scorers import tokensprocessed
from scorers import toolcalllatency
from scorers import trajectorymatcher
from scorers import turncount
from scorers.dataset_quality.scorer import DatasetQualityScorer
from scorers.remote_scorer import RemoteScorerProxy


DEFAULT_SCORERS: dict[str, type[comparator.Comparator]] = {
    "exact_match": exactmatcher.ExactMatcher,
    "recall_match": recallmatcher.RecallMatcher,
    "set_match": setmatcher.SetMatcher,
    "llmrater": llmrater.LLMRater,
    "regexp_matcher": generatedqueryregexpmatcher.GeneratedQueryRegexpMatcher,
    "returned_sql": returnedsql.ReturnedSQL,
    "executable_sql": executablesql.ExecutableGenerationScore,
    "trajectory_matcher": trajectorymatcher.TrajectoryMatcher,
    "skills_trajectory": skillstrajectorymatcher.SkillsTrajectoryMatcher,
    "skills_best_practices": skillsbestpractices.SkillsBestPractices,
    "goal_completion": goalcompletionrate.GoalCompletionRate,
    "behavioral_metrics": behavioralmetrics.BehavioralMetrics,
    "parameter_analysis": parameteranalysis.ParameterAnalysis,
    "turn_count": turncount.TurnCount,
    "agent_steps": agentsteps.AgentSteps,
    "end_to_end_latency": endtoendlatency.EndToEndLatency,
    "tool_call_latency": toolcalllatency.ToolCallLatency,
    "token_consumption": tokenconsumption.TokenConsumption,
    "tokens_processed": tokensprocessed.TokensProcessed,
    "effective_billed_tokens": effectivebilledtokens.EffectiveBilledTokens,
    "binary_rubric_scorer": binaryrubricscorer.BinaryRubricScorer,
    "python_scorer": pythonscorer.PythonScorer,
    "dataform_compile": dataformscorer.DataformCompileScorer,
    "dataform_run": dataformscorer.DataformRunScorer,
    "dataform_cloud_compile": dataformcloudscorer.DataformCloudCompileScorer,
    "dataform_cloud_run": dataformcloudscorer.DataformCloudRunScorer,
    "dbt_compile": dbtscorer.DbtCompileScorer,
    "dbt_run": dbtscorer.DbtRunScorer,
    "dataset_quality": DatasetQualityScorer,
    "analytics_scorer": analyticsscorer.AnalyticsScorer,
}


def _python_scorer_public_name(config: dict, fallback: str | None) -> str | None:
    """Match PythonScorer's configured name and script-basename fallback."""
    custom_name = config.get("scorer_name")
    if custom_name:
        return custom_name
    script_path = config.get("script_path")
    if isinstance(script_path, str):
        from_path = os.path.splitext(os.path.basename(script_path))[0].strip()
        if from_path:
            return from_path
    return fallback


def _build_instances(
    scorer_cls: type[comparator.Comparator],
    config: dict,
    experiment_config: dict,
    eval_output_item: EvalOutput,
    global_models: Any,
) -> list[comparator.Comparator]:
    """Generically instantiate a comparator class using inspect.signature."""
    config = dict(config) if isinstance(config, dict) else {}
    sig_params = inspect.signature(scorer_cls.__init__).parameters

    if (
        "database_configs" in sig_params
        or scorer_cls in (llmrater.LLMRater, pythonscorer.PythonScorer)
    ):
        config["database_configs"] = experiment_config.get(
            "database_configs", []
        )

    if scorer_cls == DatasetQualityScorer and not config.get("product_name"):
        config["product_name"] = experiment_config.get("product_name")

    if scorer_cls == binaryrubricscorer.BinaryRubricScorer:
        import json
        context_str = (
            eval_output_item.get("eval_results", "")
            if eval_output_item else ""
        )
        try:
            if isinstance(context_str, dict):
                context = context_str
            else:
                context = json.loads(context_str) if context_str else {}
            rubric = context.get("scenario", {}).get("binary_rubric", [])
            if rubric:
                return [
                    binaryrubricscorer.BinaryRubricScorer(
                        config, global_models, criterion=c, index=i
                    )
                    for i, c in enumerate(rubric)
                ]
        except Exception:
            pass
        return [binaryrubricscorer.BinaryRubricScorer(config, global_models)]

    kwargs = {}
    if "global_models" in sig_params:
        kwargs["global_models"] = global_models

    if scorer_cls == pythonscorer.PythonScorer:
        custom_name = _python_scorer_public_name(config, fallback=None)
        if custom_name:
            kwargs["name"] = custom_name

    return [scorer_cls(config, **kwargs)]


def get_scorer_instance(
    scorer_name: str,
    scorer_config: dict,
    experiment_config: dict,
    eval_output_item: EvalOutput,
    global_models: Any,
) -> list[comparator.Comparator]:
    if scorer_config is None or isinstance(scorer_config, bool):
        scorer_config = {}
    elif not isinstance(scorer_config, dict):
        return []

    # If marked delegated, proxy across reverse bidi stream
    if scorer_config.get("delegated", False):
        return [RemoteScorerProxy(scorer_name, scorer_config)]

    # Direct match in global DEFAULT_SCORERS map
    if scorer_name in DEFAULT_SCORERS:
        return _build_instances(
            DEFAULT_SCORERS[scorer_name],
            scorer_config,
            experiment_config,
            eval_output_item,
            global_models,
        )

    # Named Scorer: check `type` attribute or nested type dictionary
    target_type = scorer_config.get("type")
    cfg = scorer_config
    if not target_type:
        for default_type in DEFAULT_SCORERS:
            if (
                default_type in scorer_config
                and isinstance(scorer_config[default_type], dict)
            ):
                target_type = default_type
                cfg = scorer_config[default_type]
                break

    if target_type and target_type in DEFAULT_SCORERS:
        base_instances = _build_instances(
            DEFAULT_SCORERS[target_type],
            cfg,
            experiment_config,
            eval_output_item,
            global_models,
        )
        custom_name = scorer_config.get("scorer_name") or scorer_name
        return [
            namedscorer.NamedScorer(
                name=custom_name, base_scorer=base, target_type=target_type
            )
            for base in base_instances
        ]

    return []

def _construction_error_metric_name(scorer_name: str, config: dict) -> str:
    """Use a named scorer's public metric name even if construction fails.

    Default scorers are not wrapped. Multi-row category names are unknowable
    before construction, so this chooses the configured public scorer name.
    """
    if not isinstance(config, dict) or config.get("delegated", False):
        return scorer_name
    if scorer_name == "python_scorer":
        return _python_scorer_public_name(config, fallback=scorer_name)
    if scorer_name in DEFAULT_SCORERS:
        return scorer_name

    target_type = config.get("type")
    if not target_type:
        for default_type in DEFAULT_SCORERS:
            if isinstance(config.get(default_type), dict):
                target_type = default_type
                break

    if isinstance(target_type, str) and target_type in DEFAULT_SCORERS:
        return config.get("scorer_name") or scorer_name
    return scorer_name


def _scorer_error_row(eval_output_item, scorer_name: str, exc: Exception) -> dict:
    """Record grader failures as unscored using the existing score row fields."""
    return {
        "comparator": scorer_name,
        "score": 0,
        "comparison_error": f"{type(exc).__name__}: {exc}",
        "comparison_logs": None,
        "id": eval_output_item["id"],
        "generated_sql": eval_output_item["generated_sql"],
        "generated_error": eval_output_item["generated_error"],
        "dialects": eval_output_item["dialects"],
        "database": eval_output_item["database"],
        "job_id": eval_output_item["job_id"],
    }


def compare(
    eval_output_item: EvalOutput,
    experiment_config: dict[str, str],
    scoring_results: list[dict],
    global_models,
):
    """Evaluate independent scorers without losing good rows to another error."""
    for scorer_name, config in experiment_config["scorers"].items():
        try:
            comparators = get_scorer_instance(
                scorer_name, config, experiment_config,
                eval_output_item, global_models,
            )
        except Exception as exc:
            logging.exception("Could not construct scorer %s", scorer_name)
            public_name = _construction_error_metric_name(scorer_name, config)
            scoring_results.append(_scorer_error_row(
                eval_output_item, public_name, exc))
            continue

        for comp in comparators:
            # One configured scorer may produce multiple rows. Stage and publish
            # the entire comparator's result only after all rows serialize.
            try:
                rows = []
                try:
                    if eval_output_item["generated_sql"] is not None:
                        sig = inspect.signature(comp.compare)
                        compare_kwargs = {}
                        if "database" in sig.parameters:
                            compare_kwargs["database"] = eval_output_item.get(
                                "database", "")
                        result = comp.compare(
                            eval_output_item["nl_prompt"],
                            eval_output_item["golden_sql"],
                            eval_output_item["query_type"],
                            eval_output_item["golden_result"],
                            eval_output_item.get("golden_eval_results", ""),
                            eval_output_item["golden_error"],
                            eval_output_item["generated_sql"],
                            eval_output_item["generated_result"],
                            eval_output_item.get("eval_results", ""),
                            eval_output_item["generated_error"],
                            **compare_kwargs,
                        )
                        if isinstance(result, list):
                            rows = [(name, s, logs, None)
                                    for name, s, logs in result]
                        else:
                            score, logs = result
                            rows = [(comp.name, score, logs, None)]
                except Exception as exc:
                    # Preserve the existing comparison-error convention.
                    rows = [(comp.name, 0, None, exc)]

                if not rows:
                    rows = [(comp.name, 0, None, None)]

                staged = []
                for row_name, score_val, logs, error in rows:
                    record = comparator.ComparisonResult(
                        comp, score_val,
                        comparison_logs=logs, comparison_error=error,
                    ).to_dict()
                    record["comparator"] = row_name
                    record.update({
                        "id": eval_output_item["id"],
                        "generated_sql": eval_output_item["generated_sql"],
                        "generated_error": eval_output_item["generated_error"],
                        "dialects": eval_output_item["dialects"],
                        "database": eval_output_item["database"],
                        "job_id": eval_output_item["job_id"],
                    })
                    staged.append(record)
            except Exception as exc:
                logging.exception("Scorer %s could not serialize result", comp.name)
                staged = [_scorer_error_row(eval_output_item, comp.name, exc)]

            scoring_results.extend(staged)
