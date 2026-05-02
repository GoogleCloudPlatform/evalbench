"""LLMConsistencyComparator."""

import logging
from typing import Tuple
from generators.models import get_generator
from scorers import multi_trial_comparator
from scorers import setmatcher

CONSISTENCY_DATA_COMPARISON_PROMPT = """
We are evaluating the consistency of an AI agent in answering questions by querying a database.
We ran the agent twice for the same question, and got two different answers (SQL queries and execution results).

QUESTION: {nl_prompt}

OUTPUT #1 (Trial 1):
{trial1_execution_result}

OUTPUT #2 (Trial 2):
{trial2_execution_result}

Thinking step by step, compare the two outputs and look for differences in data and presentation.
Here are steps to follow:
1. Analyze the QUESTION: Does it explicitly ask for a specific sorting order or limit?
2. Column Mapping: Ensure that every column in OUTPUT #1 has a corresponding column in OUTPUT #2 that represents the same information.
3. Data Comparison: Compare the data within each mapped column pair.
4. Row Order: Ignore differences in row order UNLESS the QUESTION explicitly requested a specific sorting.

RULES & RELAXED EVALUATION CRITERIA:
1. The mapped column names might differ, do not make any assumptions based on them.
2. Do NOT penalize differences for any of the following reasons:
    - Column/Row Order: Differences in column names, column order, or row order when no requirements are specified in the QUESTION.
    - Rounding: Differences in integer/decimal rounding or precision.

FINAL QUESTION: Do OUTPUT #1 and OUTPUT #2 provide the same core information?
FINAL ANSWER: Choose ONLY ONE
- INFORMATION_MATCHES -- Both outputs provide the same core information (or differences fall under the acceptable relaxed criteria).
- INCONSISTENT -- The outputs contain mathematically or logically incorrect data relative to each other, or violate explicit constraints in different ways.
"""

CONSISTENCY_SQL_LOGIC_COMPARISON_PROMPT = """
We are evaluating the consistency of an AI agent in answering questions by querying a database.
We ran the agent twice for the same question, and both queries returned empty datasets. Therefore, you must evaluate the consistency of the queries based on their SQL logic.

QUESTION: {nl_prompt}

Trial 1 SQL:
{trial1_sql}

Trial 2 SQL:
{trial2_sql}

Thinking step by step, compare the two SQL queries and look for differences in logic and structure.

RULES & RELAXED EVALUATION CRITERIA:
1. The mapped column names/aliases might differ, do not make any assumptions based on them.
2. Do NOT penalize the Generated SQL if it differs for ANY of the following reasons:
    - Column/Row Order: Differences in column names, column order, or row order when no requirements are specified in the QUESTION.
    - Rounding: Differences in integer/decimal rounding or precision.

FINAL QUESTION: Is the SQL logic in Trial 1 equivalent to Trial 2?
FINAL ANSWER: Choose ONLY ONE
- EQUIVALENT -- The SQL logic is equivalent.
- NOT_EQUIVALENT -- The SQL logic is not equivalent.
"""

CONSISTENCY_ERROR_COMPARISON_PROMPT = """
We are evaluating the consistency of an AI agent in answering questions by querying a database.
We ran the agent twice for the same question, and both trials failed with errors.
Therefore, you must evaluate if the errors are equivalent in meaning, ignoring minor differences like timestamps or memory addresses.

QUESTION: {nl_prompt}

Trial 1 Error:
{trial1_error}

Trial 2 Error:
{trial2_error}

Thinking step by step, compare the two errors.
FINAL QUESTION: Do Trial 1 and Trial 2 fail for the same core reason?
FINAL ANSWER: Choose ONLY ONE
- EQUIVALENT -- The errors are equivalent in meaning.
- NOT_EQUIVALENT -- The errors are not equivalent.
"""


class LLMConsistencyComparator(multi_trial_comparator.MultiTrialComparator):
    """LLMConsistencyComparator implements the MultiTrialComparator base class.

    It uses an LLM to compare two trials.
    """

    def __init__(self, config: dict, global_models):
        super().__init__(config)
        self.name = "llm_consistency"
        self.set_match_checker = setmatcher.SetMatcher({})
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError(
                "model_config is required for LLM Consistency Comparator"
            )
        self.model = get_generator(global_models, self.model_config)

    def _is_exact_match(
        self,
        nl_prompt: str,
        trial1_query: str,
        trial1_execution_result: list,
        trial1_eval_result: str,
        trial1_error: str,
        trial2_query: str,
        trial2_execution_result: list,
        trial2_eval_result: str,
        trial2_error: str,
    ) -> bool:
        score, _ = self.set_match_checker.compare(
            nl_prompt=nl_prompt,
            golden_query=trial1_query,
            query_type="",
            golden_execution_result=trial1_execution_result,
            golden_eval_result=trial1_eval_result,
            golden_error=trial1_error,
            generated_query=trial2_query,
            generated_execution_result=trial2_execution_result,
            generated_eval_result=trial2_eval_result,
            generated_error=trial2_error,
        )
        return score == 100

    def compare(
        self,
        nl_prompt: str,
        trial1_query: str,
        trial1_execution_result: list,
        trial1_eval_result: str,
        trial1_error: str,
        trial2_query: str,
        trial2_execution_result: list,
        trial2_eval_result: str,
        trial2_error: str,
    ) -> Tuple[float, str]:
        if trial1_error and trial2_error:
            # Use LLM to compare errors if they are not strictly equal
            if trial1_error == trial2_error:
                return 100.0, "Skipped. Exact Match was found in errors."

            prompt = CONSISTENCY_ERROR_COMPARISON_PROMPT.format(
                nl_prompt=nl_prompt,
                trial1_error=trial1_error,
                trial2_error=trial2_error,
            )

            if self.model is None:
                raise RuntimeError("Model not initialized")

            response = self.model.generate(prompt)

            score = (
                100.0
                if "EQUIVALENT" in response and "NOT_EQUIVALENT" not in response
                else 0.0
            )
            return score, response

        elif trial1_error or trial2_error:
            # One has error, other doesn't
            return (
                0.0,
                (
                    "Inconsistent: One trial failed and the other didn't. Trial 1"
                    f" Error: {trial1_error}, Trial 2 Error: {trial2_error}"
                ),
            )

        is_empty_results = (
            len(trial1_execution_result) == 0 and len(
                trial2_execution_result) == 0
        )

        if not is_empty_results and self._is_exact_match(
            nl_prompt,
            trial1_query,
            trial1_execution_result,
            trial1_eval_result,
            trial1_error,
            trial2_query,
            trial2_execution_result,
            trial2_eval_result,
            trial2_error,
        ):
            return 100.0, "Skipped. Exact Match was found."

        if is_empty_results:
            prompt = CONSISTENCY_SQL_LOGIC_COMPARISON_PROMPT.format(
                nl_prompt=nl_prompt,
                trial1_sql=trial1_query,
                trial2_sql=trial2_query,
            )
        else:
            prompt = CONSISTENCY_DATA_COMPARISON_PROMPT.format(
                nl_prompt=nl_prompt,
                trial1_execution_result=trial1_execution_result[
                    :50
                ],  # Limit to first 50
                trial2_execution_result=trial2_execution_result[:50],
            )

        logging.debug(
            "\n --------- consistency prompt:   --------- \n %s ", prompt)

        if self.model is None:
            raise RuntimeError("Model not initialized")

        response = self.model.generate(prompt)

        logging.debug(
            "\n --------- llm_consistency_output:   --------- \n %s ", response
        )

        # Scoring Logic
        if is_empty_results:
            score = (
                100.0
                if "EQUIVALENT" in response and "NOT_EQUIVALENT" not in response
                else 0.0
            )
        else:
            score = (
                100.0
                if "INFORMATION_MATCHES" in response
                and "INCONSISTENT" not in response
                else 0.0
            )

        return score, response
