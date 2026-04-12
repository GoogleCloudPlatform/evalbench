"""
LLMRater
In this comparison strategy, an LLM compares the golden execution results with the generated sql execution results.
It returns a score between 0 and 100, with a score of 100 for concrete positive cases,
where either there is a mismatch of columns names, extra relevant columns, or harmless unrequested sorting/limits in Generated SQL.

Evaluation rules given to LLM:
    1. Assume OUTPUT #1 is the gold standard and is ALWAYS correct.
    2. The order of columns in OUTPUT #2 does not matter.
    3. The order of rows in OUTPUT #2 does not matter UNLESS explicitly requested in the prompt.
    4. Allow slight variations due to differences in rounding or precision, for calculated values.
    5. The mapped column names might differ, do not make any assumptions based on them.

Run Configuration Options:
    1. model_config: Required
        - File that defines the configuration settings for the LLM model to be used in evaluation.
"""

from typing import Tuple
from generators.models import get_generator
from scorers import setmatcher
import logging

from scorers import comparator
from .util import make_hashable, with_cache_execute
from databases.util import get_cache_client

ERROR_CATEGORIZATION_PROMPT = """
You are an expert SQL evaluator. Your task is to analyze a "Generated SQL" query against a "Golden SQL" (ground truth) query and their respective execution results.

### Input Data
**NL Prompt:** {nl_prompt}
**Golden SQL:** {golden_sql}
**Golden Result:** {golden_execution_result}
**Generated SQL:** {generated_sql}
**Generated Result:** {generated_execution_result}

### Task
Compare the queries and results to identify specific errors in the Generated SQL. If the Generated SQL is functionally equivalent to the Golden SQL (even if syntax differs), mark it as correct.

### Error Taxonomy
If errors exist, categorize them using ONLY the following tags:

1. [EntityError] - Wrong table or entity was used.
2. [ValueLinkingError] - Wrong literal value (string/number) was used.
3. [ColumnLinkingError] - Wrong column was selected or used in a condition.
4. [OrderingError] - Sorting order (ASC/DESC) or column is incorrect (only flag if prompt explicitly requested sorting).
5. [InstructionError] - Failed to follow specific constraints in the prompt (e.g., "return top 5").
6. [IntentError] - Misinterpreted the user's fundamental request.
7. [DataTypesError] - Incorrect handling of data types (e.g., casting, dates).
8. [CountingError] - Aggregation or counting logic is flawed.
9. [FilterError] - Correct columns used, but wrong logical operator or filter condition.
10. [LogicError] - Fundamental logic flaw not covered by other categories (e.g., wrong join type).
11. [OtherError] - Any other error not covered by the above categories.

### Output Format
Provide your response in the following format:

**Reasoning:**
<Analyze the differences between the queries and results here>

**Tags & Explanations:**
<Tag 1>: <One-line explanation of the specific error>
<Tag 2>: <One-line explanation of the specific error>
"""


class LLMRater(comparator.Comparator):
    """
    LLMRater class implements the Comparator base class.

    Attributes:
        1. name: Name of the comparator. Set to "llmrater"
        2. model_config: File that defines the configuration settings for the LLM model used in evaluation.
    """

    def __init__(self, config: dict, global_models):
        self.name = "llmrater"
        self.set_match_checker = setmatcher.SetMatcher({})
        self.cache_client = get_cache_client(config)
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for LLM Rater")
        self.model = get_generator(global_models, self.model_config)

    def _is_exact_match(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: list,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: list,
        generated_eval_result: str,
        generated_error: str,
    ):
        score, _ = self.set_match_checker.compare(
            nl_prompt,
            golden_query,
            query_type,
            golden_execution_result,
            golden_eval_result,
            golden_error,
            generated_query,
            generated_execution_result,
            generated_eval_result,
            generated_error,
        )
        return score == 100

    def _inference_without_caching(self, prompt):
        if self.model is None:
            raise RuntimeError("Model not initialized")
        return self.model.generate(prompt)

    @staticmethod
    def take_n_uniques(output_list: list, n: int) -> list:
        """Takes n number of unique (non duplicate) values from the output list.

        Args:
          output_list: The execution output result set
          n: Max number of unique values needed.

        Returns:
          The execution output result set without duplicates in a size of n values or less.
        """
        seen_dicts = set()
        new_list = []
        for d in output_list:
            # Convert the dictionary to a hashable frozenset for efficient lookup
            t = frozenset((k, make_hashable(v)) for k, v in d.items())
            if t not in seen_dicts:
                seen_dicts.add(t)
                new_list.append(d)
                if len(new_list) == n:
                    break
        return new_list

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: list,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: list,
        generated_eval_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        if self._is_exact_match(
            nl_prompt,
            golden_query,
            query_type,
            golden_execution_result,
            golden_eval_result,
            golden_error,
            generated_query,
            generated_execution_result,
            generated_eval_result,
            generated_error,
        ):
            return 100, "Skipped. Exact Match was found."

        if golden_error:
            return 0, "Golden query failed to execute."
        if generated_error:
            return 0, "Generated query failed to execute."

        only_first_n = 50

        golden_execution_result = self.take_n_uniques(
            golden_execution_result, only_first_n
        )
        generated_execution_result = self.take_n_uniques(
            generated_execution_result, only_first_n
        )

        prompt = f"""
        We are trying to answer this question by querying a database:

        QUESTION: {nl_prompt}

        The correct answer to this question is:

        OUTPUT #1 (Gold Standard):

        {golden_execution_result}


        We get the following answer from a generated query:

        OUTPUT #2 (Generated Result):

        {generated_execution_result}


        Thinking step by step, compare the two outputs and look for differences in data and presentation.
        Here are steps to follow:

        1. Analyze the QUESTION: Does it explicitly ask for a specific sorting order (e.g., "ordered by date", "top 5")? Does it explicitly ask for a limit?
        2. Column Mapping: Ensure that every column in OUTPUT #1 has a corresponding column in OUTPUT #2 that represents the same information. OUTPUT #2 is allowed to have additional descriptive columns.
        3. Data Comparison: Compare the data within each mapped column pair.
        4. Row Order: Ignore differences in row order UNLESS the QUESTION explicitly requested a specific sorting. Treat the data as unordered sets if no order is specified.
        5. Extra Rows: If OUTPUT #2 has extra rows but contains all of OUTPUT #1, evaluate if the extra rows violate the prompt's constraints. If the prompt was ambiguous about limits (e.g. "Identify the MSA with the highest growth" and the model returns a ranked list instead of a single row), treating it as EXTRA_INFORMATION is acceptable and correct.

        RULES & RELAXED EVALUATION CRITERIA - These MUST be strictly followed:

        1. Assume OUTPUT #1 is the gold standard and its core data values are ALWAYS mathematically/logically correct.
        2. The mapped column names might differ, do not make any assumptions based on them.
        3. Do NOT penalize OUTPUT #2 if it differs from OUTPUT #1 for ANY of the following reasons:
            - Column/Row Order: Differences in column names, column order, or row order when no requirements are specified in the QUESTION.
            - Rounding: Differences in integer/decimal rounding or precision when the QUESTION lacks specific guidelines.
            - Ambiguous Limit: The QUESTION asks for "top/highest" or "bottom/lowest" entries but doesn't specify a concrete limit, leading to different numbers of entries.
            - Entity Representation: The QUESTION asks for a list of items but doesn't specify IDs or names, leading one output to return IDs and the other names.
            - Extra Columns: OUTPUT #2 has a small number of extra columns that are not explicitly excluded and don't render the overall result incorrect.

        FINAL QUESTION: Does OUTPUT #2 provide the same information as OUTPUT #1?
        FINAL ANSWER: Choose ONLY ONE
        - INFORMATION_MATCHES -- OUTPUT #1 and OUTPUT #2 provide the same core information (or differences fall under the acceptable relaxed criteria).
        - MISSING_INFORMATION -- Something important requested by the QUESTION is missing from OUTPUT #2 (e.g. data points dropped, missing expected columns).
        - EXTRA_INFORMATION -- OUTPUT #2 includes the correct answer but added non-harmful extra relevant columns, or harmless extra rows due to an ambiguous limit/sorting constraint in the QUESTION.
        - INCORRECT_INFORMATION -- OUTPUT #2 contains mathematically or logically incorrect data, wrong aggregations, bad joins, missing expected rows, or violates explicit constraints in the QUESTION.
        """

        logging.debug("\n --------- prompt:   --------- \n %s ", prompt)

        if self.cache_client:
            response = with_cache_execute(
                prompt,
                self.model_config,
                self._inference_without_caching,
                self.cache_client,
            )
        else:
            response = self._inference_without_caching(prompt)

        logging.debug(
            "\n --------- llm_rater_output:   --------- \n %s ", response)

        # Scoring Logic: Both INFORMATION_MATCHES and EXTRA_INFORMATION are rewarded as correct.
        score = (
            100
            if ("INFORMATION_MATCHES" in response or "EXTRA_INFORMATION" in response)
            else 0
        )

        if score == 0:
            prompt = ERROR_CATEGORIZATION_PROMPT.format(
                nl_prompt=nl_prompt,
                golden_sql=golden_query,
                golden_execution_result=golden_execution_result,
                generated_sql=generated_query,
                generated_execution_result=generated_execution_result,
            )
            if self.cache_client:
                error_categorization_response = with_cache_execute(
                    prompt,
                    self.model_config,
                    self._inference_without_caching,
                    self.cache_client,
                )
            else:
                error_categorization_response = self._inference_without_caching(
                    prompt)

            response += "\nError analysis:\n\n" + error_categorization_response

        return score, response
