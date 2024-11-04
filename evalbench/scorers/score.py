"""Performs the compare operation."""

from scorers import comparator
from scorers import exactmatcher
from scorers import generatedqueryregexpmatcher
from scorers import recallmatcher
from scorers import vertextmatcher
from scorers import llmrater
from scorers import returnedsql
from dataset.evaloutput import EvalOutput
import logging


def compare(eval_output_item: EvalOutput, experiment_config: dict[str, str], scoring_results: list[dict]):
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
        comparators.append(recallmatcher.RecallMatcher(scorers["recall_match"]))
    if "vertexmatcher" in scorers:
        comparators.append(vertextmatcher.VertexMatcher(scorers["vertexmatcher"]))
    if "llmrater" in scorers:
        comparators.append(llmrater.LLMRater(scorers["llmrater"]))
    if "regexp_matcher" in scorers:
        comparators.append(
            generatedqueryregexpmatcher.GeneratedQueryRegexpMatcher(scorers["regexp_matcher"])
        )
    if "returned_sql" in scorers:
        comparators.append(returnedsql.ReturnedSQL(scorers["returned_sql"]))

    for comp in comparators:
        score = 0
        comparison_result = comparator.ComparisonResult(comp, 0)
        try:
            if eval_output_item["generated_sql"] is not None:
                if eval_output_item["query_type"] in ["dml", "ddl"]:
                    generated_result = eval_output_item["eval_results"]
                    golden_result = eval_output_item["golden_eval_results"]
                else:
                    generated_result = eval_output_item["generated_result"]
                    golden_result = eval_output_item["golden_result"]

                score, logs = comp.compare(
                    eval_output_item["nl_prompt"],
                    eval_output_item["golden_sql"],
                    golden_result,
                    eval_output_item["generated_sql"],
                    generated_result,
                )
                comparison_result.score = score
                comparison_result.comparison_logs = logs
        except Exception as e:
            comparison_result.comparison_error = e
        score_dict = comparison_result.to_dict()
        score_dict["id"] = eval_output_item["id"]
        score_dict["generated_sql"] = eval_output_item["generated_sql"]
        score_dict["generated_error"] = eval_output_item["generated_error"]
        score_dict["job_id"] = eval_output_item["job_id"]
        logging.debug('scoring: %d %s %d', score_dict["id"], comp.name, score)
        scoring_results.append(score_dict)
