"""Simple comparison strategy that checks if the two execution results are exactly the same."""

from typing import Tuple
import json
import logging

from scorers import comparator
import vertexai
from vertexai.preview.generative_models import GenerationConfig, GenerativeModel


class VertexMatcher(comparator.Comparator):
    """VertexMatcher.

    Attributes:
      name:
    """

    def __init__(self, config: dict):
        """Constructor.

        Args:
            config: Configuration dictionary.
        """
        self.name = "vertexmatcher"
        self.config = config
        vertexai.init(
            project=self.config["gcp_project_id"],
            location=self.config["gcp_project_location"],
        )
        self.generation_config = GenerationConfig(temperature=0)
        self.model = GenerativeModel(self.config["model"])

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
        Here's the task: Evaluate if the LLM-generated SQL query is semantically
        equivalent to the ground-truth SQL query.
        Consider both the SQL syntax and their execution results.

        **Inputs:**

        * **Ground-truth SQL:** {golden_query}
        * **LLM Generated SQL:** {generated_query}
        * **Ground-truth Results:** {golden_execution_result}
        * **LLM Generated Results:** {generated_execution_result}

        **Output Format:**

        Generate a valid, parseable JSON. Do not include any extra text outside of the JSON string. object with:

        * **score:** A number between 0 and 100.
            * 100: LLM query is semantically equivalent to the ground-truth.
            * 0: LLM query is entirely incorrect.
        * **explain:** A clear explanation of your scoring decision.

        **Step-by-Step Evaluation:**

        1. **Analyze SQL Queries:**
        * Do they have the same structure (tables, joins, WHERE clauses, etc.)?
        * Are there any logical differences that would produce different results?

        2. **Compare Execution Results:**
        * Do the results contain the same columns and data types?
        * Are the returned rows identical? Consider the ordering of the rows if order is important.

        **Important:**  Be specific in your 'explain' field. Don't just say "They match";
        describe *why* they match or *how* they differ. Escape quotes in the 'explain' field.


        """
        logging.debug("\n --------- prompt:   --------- \n %s ", prompt)
        response = self.model.generate_content(
            prompt, generation_config=self.generation_config
        )
        json_output = (
            response.text.replace("```json", "").replace("```", "").replace("\n", " ")
        )
        logging.debug("\n --------- json_output:   --------- \n %s ", json_output)
        score = json.loads(json_output)["score"]
        logs = json.loads(json_output)["explain"]
        return score, logs
