import inspect
import logging
import os
from typing import Any

from dataset.evaloutput import EvalOutput
from scorers import agentsteps
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
}


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
        custom_name = config.get("scorer_name")
        script_path = config.get("script_path")
        if not custom_name and script_path and isinstance(script_path, str):
            custom_name = os.path.splitext(
                os.path.basename(script_path)
            )[0].strip()
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
    """Resolve comparator instances for a given scorer config entry."""
    if not isinstance(scorer_config, dict):
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


def compare(
    eval_output_item: EvalOutput,
    experiment_config: dict[str, str],
    scoring_results: list[dict],
    global_models,
):
    """Run comparators against eval output."""
    scorers = experiment_config["scorers"]
    comparators: list[comparator.Comparator] = []

    for name, config in scorers.items():
        comparators.extend(
            get_scorer_instance(
                name,
                config,
                experiment_config,
                eval_output_item,
                global_models,
            )
        )

    for comp in comparators:
        # Each element is (comparator_name, score, logs, error). Most
        # comparators produce one row, but a whole-dataset scorer like
        # dataset_quality returns a list of rows (one per category).
        rows: list = []
        try:
            if eval_output_item["generated_sql"] is not None:
                sig = inspect.signature(comp.compare)
                compare_kwargs = {}
                if "database" in sig.parameters:
                    compare_kwargs["database"] = eval_output_item.get(
                        "database", ""
                    )
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
                    rows = [(name, s, logs, None) for name, s, logs in result]
                else:
                    score, logs = result
                    rows = [(comp.name, score, logs, None)]
        except Exception as e:
            rows = [(comp.name, 0, None, e)]
        if not rows:
            rows = [(comp.name, 0, None, None)]
        for name, score_val, logs, error in rows:
            score_dict = comparator.ComparisonResult(
                comp, score_val, comparison_logs=logs, comparison_error=error
            ).to_dict()
            score_dict["comparator"] = name
            score_dict.update({
                "id": eval_output_item["id"],
                "generated_sql": eval_output_item["generated_sql"],
                "generated_error": eval_output_item["generated_error"],
                "dialects": eval_output_item["dialects"],
                "database": eval_output_item["database"],
                "job_id": eval_output_item["job_id"],
            })
            logging.debug(
                "scoring: %s %s %s", score_dict["id"], name, score_val
            )
            scoring_results.append(score_dict)
