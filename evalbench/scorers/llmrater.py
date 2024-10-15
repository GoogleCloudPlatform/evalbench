"""
Currently the LLM Rater compares the golden execution results with the
generated sql execution results. It returns a score of 100 for concrete
positive cases, where either there is a Mismatch of Columns names or Extra Relevant
Columns in Generated SQL exists.
"""

import logging
import backoff
from typing import Tuple

from ratelimit import limits, sleep_and_retry, exception
from scorers import comparator
import vertexai
from vertexai.preview.generative_models import GenerationConfig, GenerativeModel


class LLMRater(comparator.Comparator):
    """LLMRater.

    Attributes:
      name:
    """

    def __init__(self, config: dict):
        """Constructor.

        Args:
            config: Configuration dictionary.
        """
        self.name = "llmrater"
        self.config = config
        vertexai.init(
            project=self.config["gcp_project_id"],
            location=self.config["gcp_project_location"],
        )
        self.generation_config = GenerationConfig(temperature=0)
        self.model = GenerativeModel(self.config["model"])

    @backoff.on_exception(backoff.constant, exception=Exception,
                          max_tries=8, interval=80, jitter=backoff.full_jitter)
    @limits(calls=30, period=60)
    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        golden_execution_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        only_first_n = 50
        if len(golden_execution_result) > only_first_n:
            golden_execution_result = golden_execution_result[:only_first_n]
        if len(generated_execution_result) > only_first_n:
            generated_execution_result = generated_execution_result[:only_first_n]

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

        1. Map all columns in OUTPUT #1 to the same columns in OUTPUT #2. Column names might differ as
           long as the data they contain represents the same information.
        2. All columns present in OUTPUT #1 must also be present in OUTPUT #2. OUTPUT #2 is allowed to
           have additional columns relevant to the query.
        3. Compare the data in the mapped columns between OUTPUT #1 and OUTPUT #2. The data should be
           an exact match; there should be no extra or missing data in OUTPUT #2.
        4. Minor string transformations are allowed (e.g., concatenating first and last name), but the
           underlying information must be preserved.
        5. Only compare the result sets; do not make any assumptions beyond the data presented.


        RULES - These MUST be strictly followed, to answer the FINAL QUESTION:

        1. Assume OUTPUT #1 is the gold standard and is ALWAYS correct.
        2. The order of columns in OUTPUT #2 does not matter.
        3. Numbers, dates, timestamps, measurements, and metrics MUST match EXACTLY between the two outputs.


        FINAL QUESTION: Does OUTPUT #2 provide the same information as OUTPUT #1?
        FINAL ANSWER: Choose ONLY ONE
        - INFORMATION_MATCHES -- OUTPUT #1 and OUTPUT #2 provide the same information.
        - MISSING_INFORMATION -- Something important is missing from OUTPUT #2.
        - EXTRA_INFORMATION -- Some non-harmful extra relevant columns were added to OUTPUT #2.
        - INCORRECT_INFORMATION -- Some incorrect information was added to OUTPUT #2, likely due to
          an incorrect filter or incorrect aggregation.
        """

        logging.debug("\n --------- prompt:   --------- \n %s ", prompt)
        response = self.model.generate_content(
            prompt, generation_config=self.generation_config
        ).text

        logging.debug("\n --------- llm_rater_output:   --------- \n %s ", response)
        score = (
            100
            if ("INFORMATION_MATCHES" in response or "EXTRA_INFORMATION" in response)
            else 0
        )
        return score, response
