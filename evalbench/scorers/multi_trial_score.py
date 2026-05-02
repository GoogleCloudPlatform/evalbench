"""Performs multi-trial scoring with transitivity."""

import logging
from typing import Any, Callable, List, Tuple
from scorers import exact_match_consistency_comparator
from scorers import llm_consistency_comparator
from scorers import multi_trial_comparator


def compare(
    prompt_id: str,
    nl_prompt: str,
    trials: List[dict],
    experiment_config: dict,
    global_models,
) -> List[dict]:
    """Run multi-trial scorers against a list of trials for the same prompt.

    Args:
      prompt_id: The ID of the prompt.
      nl_prompt: The natural language prompt.
      trials: A list of dicts, each representing a trial's output.
      experiment_config: Config for the scorers to run.
      global_models: The global models for LLM calls.

    Returns:
      A list of dicts containing pairwise consistency scores.
    """
    scorers = experiment_config.get("scorers", {})
    comparators: List[multi_trial_comparator.MultiTrialComparator] = []

    if "exact_match_consistency" in scorers:
        comparators.append(
            exact_match_consistency_comparator.ExactMatchConsistencyComparator(
                scorers["exact_match_consistency"]
            )
        )
    if "llm_consistency" in scorers:
        comparators.append(
            llm_consistency_comparator.LLMConsistencyComparator(
                scorers["llm_consistency"], global_models
            )
        )

    if not comparators:
        return []

    num_trials = len(trials)
    pairwise_results = []

    for comp in comparators:
        # Initialize score matrix: num_trials x num_trials
        score_mat = [[None for _ in range(num_trials)]
                     for _ in range(num_trials)]

        # Self-consistency is always 100
        for i in range(num_trials):
            score_mat[i][i] = (100.0, "Self-consistency")

        def get_score(i, j):
            if score_mat[i][j] is not None:
                return score_mat[i][j]

            t1 = trials[i]
            t2 = trials[j]
            t1_error = (
                t1.get("generated_error")
                or t1.get("sql_generator_error")
                or t1.get("prompt_generator_error")
            )
            t2_error = (
                t2.get("generated_error")
                or t2.get("sql_generator_error")
                or t2.get("prompt_generator_error")
            )

            score, logs = comp.compare(
                nl_prompt,
                t1.get("generated_sql"),
                t1.get("generated_result"),
                t1.get("eval_results", ""),
                t1_error,
                t2.get("generated_sql"),
                t2.get("generated_result"),
                t2.get("eval_results", ""),
                t2_error,
            )
            score_mat[i][j] = (score, logs)
            score_mat[j][i] = (score, logs)  # Symmetric
            return score, logs

        # 1. Calculate adjacent pairs first to enable transitivity
        for i in range(num_trials - 1):
            get_score(i, i + 1)

        def infer_transitive_scores():
            for k in range(num_trials):
                for i in range(num_trials):
                    for j in range(num_trials):
                        if score_mat[i][j] is not None:
                            continue
                        if i >= j:
                            continue

                        # If i~k and k~j, then i~j
                        if score_mat[i][k] is not None and score_mat[k][j] is not None:
                            s1, l1 = score_mat[i][k]
                            s2, l2 = score_mat[k][j]
                            # Transitivity:
                            # 1. If both are consistent (100), then inferred is consistent (100).
                            # 2. If one is consistent (100) and other is inconsistent (0), then inferred is inconsistent (0).
                            if s1 == 100.0 and s2 == 100.0:
                                score_mat[i][j] = (
                                    100.0,
                                    f"Inferred from {i}-{k} and {k}-{j}",
                                )
                                score_mat[j][i] = score_mat[i][j]
                            elif (s1 == 100.0 and s2 == 0.0) or (s1 == 0.0 and s2 == 100.0):
                                score_mat[i][j] = (
                                    0.0,
                                    f"Inferred from {i}-{k} and {k}-{j}",
                                )
                                score_mat[j][i] = score_mat[i][j]

        # 2. Infer transitive scores
        infer_transitive_scores()

        # 3. Fill remaining missing scores and infer iteratively
        for i in range(num_trials):
            for j in range(num_trials):
                if i < j and score_mat[i][j] is None:
                    get_score(i, j)
                    infer_transitive_scores()

        # Collect results
        for i in range(num_trials):
            for j in range(num_trials):
                if i < j:
                    score, logs = score_mat[i][j]
                    pairwise_results.append({
                        "prompt_id": prompt_id,
                        "trial1_id": trials[i].get("id"),
                        "trial2_id": trials[j].get("id"),
                        "comparator": comp.name,
                        "score": score,
                        "logs": logs,
                    })

    return pairwise_results
