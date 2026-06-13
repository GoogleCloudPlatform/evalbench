"""Performs the compare operation."""

from scorers import comparator
from scorers import exactmatcher
from scorers import generatedqueryregexpmatcher
from scorers import recallmatcher
from scorers import setmatcher
from scorers import llmrater
from scorers import returnedsql
from scorers import executablesql
from scorers import trajectorymatcher
from scorers import skillstrajectorymatcher
from scorers import goalcompletionrate
from scorers import behavioralmetrics
from scorers import parameteranalysis
from scorers import skillsbestpractices
from scorers import turncount
from scorers import endtoendlatency
from scorers import toolcalllatency
from scorers import tokenconsumption
from scorers import binaryrubricscorer
from scorers import pythonscorer
from scorers import dataformscorer
from scorers import dataformcloudscorer
from scorers import dbtscorer
from dataset.evaloutput import EvalOutput
import logging
import os


def compare(
    eval_output_item: EvalOutput,
    experiment_config: dict[str, str],
    scoring_results: list[dict],
    global_models,
):
    """Run comparators against eval output.

    Args:
      eval_output_item: EvalItemOutput object to compare.
      experiment_config: Config for the scorers to run.
    """
    scorers = experiment_config["scorers"]
    comparators: list[comparator.Comparator] = []
    if "exact_match" in scorers:
        comparators.append(exactmatcher.ExactMatcher(scorers["exact_match"]))
    if "recall_match" in scorers:
        comparators.append(
            recallmatcher.RecallMatcher(scorers["recall_match"]))
    if "set_match" in scorers:
        comparators.append(setmatcher.SetMatcher(scorers["set_match"]))
    if "llmrater" in scorers:
        comparators.append(llmrater.LLMRater(
            scorers["llmrater"], global_models))
    if "regexp_matcher" in scorers:
        comparators.append(
            generatedqueryregexpmatcher.GeneratedQueryRegexpMatcher(
                scorers["regexp_matcher"]
            )
        )
    if "returned_sql" in scorers:
        comparators.append(returnedsql.ReturnedSQL(scorers["returned_sql"]))
    if "executable_sql" in scorers:
        comparators.append(
            executablesql.ExecutableGenerationScore(scorers["executable_sql"])
        )
    if "trajectory_matcher" in scorers:
        comparators.append(
            trajectorymatcher.TrajectoryMatcher(scorers["trajectory_matcher"])
        )
    if "skills_trajectory" in scorers:
        comparators.append(
            skillstrajectorymatcher.SkillsTrajectoryMatcher(scorers["skills_trajectory"])
        )
    if "skills_best_practices" in scorers:
        comparators.append(
            skillsbestpractices.SkillsBestPractices(
                scorers["skills_best_practices"], global_models
            )
        )
    if "goal_completion" in scorers:
        comparators.append(
            goalcompletionrate.GoalCompletionRate(
                scorers["goal_completion"], global_models
            )
        )
    if "behavioral_metrics" in scorers:
        comparators.append(
            behavioralmetrics.BehavioralMetrics(
                scorers["behavioral_metrics"], global_models
            )
        )
    if "parameter_analysis" in scorers:
        comparators.append(
            parameteranalysis.ParameterAnalysis(
                scorers["parameter_analysis"], global_models
            )
        )
    if "turn_count" in scorers:
        comparators.append(
            turncount.TurnCount(scorers["turn_count"])
        )
    if "end_to_end_latency" in scorers:
        comparators.append(
            endtoendlatency.EndToEndLatency(scorers["end_to_end_latency"])
        )
    if "tool_call_latency" in scorers:
        comparators.append(
            toolcalllatency.ToolCallLatency(scorers["tool_call_latency"])
        )
    if "token_consumption" in scorers:
        comparators.append(
            tokenconsumption.TokenConsumption(scorers["token_consumption"])
        )
    if "binary_rubric_scorer" in scorers:
        import json

        context_str = eval_output_item.get("eval_results", "")
        try:
            if isinstance(context_str, dict):
                context = context_str
            else:
                context = json.loads(context_str) if context_str else {}
            rubric = context.get("scenario", {}).get("binary_rubric", [])
            if rubric:
                for index, criterion in enumerate(rubric):
                    comparators.append(
                        binaryrubricscorer.BinaryRubricScorer(
                            scorers["binary_rubric_scorer"], global_models,
                            criterion=criterion, index=index
                        )
                    )
            else:
                comparators.append(
                    binaryrubricscorer.BinaryRubricScorer(
                        scorers["binary_rubric_scorer"], global_models
                    )
                )
        except Exception:

            comparators.append(
                binaryrubricscorer.BinaryRubricScorer(
                    scorers["binary_rubric_scorer"], global_models
                )
            )
    for key, scorer_config in scorers.items():
        if key == "python_scorer":
            custom_name = scorer_config.get("scorer_name")
            if custom_name and isinstance(custom_name, str):
                custom_name = custom_name.strip()
            if not custom_name:
                script_path = scorer_config.get("script_path")
                if script_path and isinstance(script_path, str) and script_path.strip():
                    custom_name = os.path.splitext(os.path.basename(script_path))[0].strip()
                if not custom_name:
                    custom_name = key
            comparators.append(pythonscorer.PythonScorer(scorer_config, name=custom_name))
    if "dataform_compile" in scorers:
        comparators.append(
            dataformscorer.DataformCompileScorer(scorers["dataform_compile"])
        )
    if "dataform_run" in scorers:
        comparators.append(
            dataformscorer.DataformRunScorer(scorers["dataform_run"])
        )
    if "dataform_cloud_compile" in scorers:
        comparators.append(
            dataformcloudscorer.DataformCloudCompileScorer(
                scorers["dataform_cloud_compile"]
            )
        )
    if "dataform_cloud_run" in scorers:
        comparators.append(
            dataformcloudscorer.DataformCloudRunScorer(
                scorers["dataform_cloud_run"]
            )
        )
    if "dbt_compile" in scorers:
        comparators.append(
            dbtscorer.DbtCompileScorer(scorers["dbt_compile"])
        )
    if "dbt_run" in scorers:
        comparators.append(
            dbtscorer.DbtRunScorer(scorers["dbt_run"])
        )

    for comp in comparators:
        score = 0
        comparison_result = comparator.ComparisonResult(comp, 0)
        try:
            if eval_output_item["generated_sql"] is not None:
                score, logs = comp.compare(
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
                )
                comparison_result.score = score
                comparison_result.comparison_logs = logs
        except Exception as e:
            comparison_result.comparison_error = e
        score_dict = comparison_result.to_dict()
        score_dict["id"] = eval_output_item["id"]
        score_dict["generated_sql"] = eval_output_item["generated_sql"]
        score_dict["generated_error"] = eval_output_item["generated_error"]
        score_dict["dialects"] = eval_output_item["dialects"]
        score_dict["database"] = eval_output_item["database"]
        score_dict["job_id"] = eval_output_item["job_id"]
        logging.debug("scoring: %d %s %d", score_dict["id"], comp.name, score)
        scoring_results.append(score_dict)
