"""
LLMRater
In this comparison strategy, an LLM compares the golden execution results with the generated sql execution results.
It returns a score between 0 and 10, with a score of 100 for concrete positive cases,
where either there is a mismatch of columns names or extra relevant columns in Generated SQL exists.

Evaluation rules given to LLM:
    1. Assume OUTPUT #1 is the gold standard and is ALWAYS correct.
    2. The order of columns in OUTPUT #2 does not matter.
    3. Allow slight variations due to differences in rounding or precision, for calculated values.
        Otherwise ensure exact matches for numbers, dates, timestamps, measurements, and metrics
        between the two outputs.
    4. The mapped column names might differ, do not make any assumptions based on them.

Run Configuration Options:
    1. gcp_project_id: Required
        - A valid GCP project ID which will be used for querying the LLM
    2. gcp_project_location: Required
        - A valid region from where the vertexAI resources will be utilized
    3. model: Required
        - An LLM model to use for evaluation. e.g. gemini-1.5-pro-002
"""

from typing import Tuple
from generators.models import get_generator
from scorers import exactmatcher
import logging
from util.config import load_yaml_config

from scorers import comparator
from .util import make_hashable, with_cache_execute
from databases.util import get_cache_client


class LLMRater(comparator.Comparator):
    """
    LLMRater class implements the Comparator base class.

    Attributes:
        1. name: Name of the comparator. Set to "vertexmatcher"
        2. config: Scorer config defined in the run config yaml file
        3. gcp_project_id: ID of the GCP project ID from where the resources will be utilized
        4. gcp_project_location: A valid region for vertexAI resources
        5. model: The LLM model to use
    """

    def __init__(self, config: dict):
        self.name = "llmrater"
        self.exact_match_checker = exactmatcher.ExactMatcher({})
        self.cache_client = get_cache_client(config)
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for LLM Rater")
        self.model = get_generator(load_yaml_config(self.model_config))

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
        score, _ = self.exact_match_checker.compare(
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

        OUTPUT #1:

        {golden_execution_result}


        We get the following answer from a second query.

        OUTPUT #2

        {generated_execution_result}


        Thinking step by step, compare the two outputs and look for differences in data presentation.
        Here are steps to follow:

        1. Ensure that every column in OUTPUT #1 has a corresponding column in OUTPUT #2 that represents
           the same information, even if the column names are different.
        2. All columns present in OUTPUT #1 must also be present in OUTPUT #2. OUTPUT #2 is allowed to
           have additional columns relevant to the query.
        3. Compare the data within each mapped column pair between OUTPUT #1 and OUTPUT #2.
           Ensure that OUTPUT #2 contains all the data from OUTPUT #1, with no missing or extra rows.
        4. Minor string transformations are allowed (e.g., concatenating first and last name), but the
           underlying information must be preserved.

        RULES - These MUST be strictly followed, to answer the FINAL QUESTION:

        1. Assume OUTPUT #1 is the gold standard and is ALWAYS correct.
        2. The order of columns in OUTPUT #2 does not matter.
        3. Allow slight variations due to differences in rounding or precision, for calculated values.
           Otherwise ensure exact matches for numbers, dates, timestamps, measurements, and metrics
           between the two outputs.
        4. The mapped column names might differ, do not make any assumptions based on them.

        FINAL QUESTION: Does OUTPUT #2 provide the same information as OUTPUT #1?
        FINAL ANSWER: Choose ONLY ONE
        - INFORMATION_MATCHES -- OUTPUT #1 and OUTPUT #2 provide the same information.
        - MISSING_INFORMATION -- Something important is missing from OUTPUT #2.
        - EXTRA_INFORMATION -- Some non-harmful extra relevant columns were added to OUTPUT #2.
        - INCORRECT_INFORMATION -- Some incorrect information was added to OUTPUT #2, likely due to
          an incorrect filter or incorrect aggregation.
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

        logging.debug("\n --------- llm_rater_output:   --------- \n %s ", response)
        score = (
            100
            if ("INFORMATION_MATCHES" in response or "EXTRA_INFORMATION" in response)
            else 0
        )
        return score, response
