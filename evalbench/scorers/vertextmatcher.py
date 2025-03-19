"""
VertexMatcher

This comparison strategy uses an LLM to find the extent to which the generated and expected queries are semantically equivalent.
It considers both, the SQL syntax, and their execution results

The LLM returns a score between 0 and 100 with 100 meaning generated query is sematically equivalent to the ground truth query,
and 0 meaning that both the queries are entirely different.

The LLM also returns a detailed explanation of its scoring decision.

Evaluation guidelines given to LLM:
    1. Analyze SQL Queries:
        - Do they have the same structure (tables, joins, WHERE clauses, etc.)?
        - Are there any logical differences that would produce different results?

    2. Compare Execution Results:
        - Do the results contain the same columns and data types?
        - Are the returned rows identical? Consider the ordering of the rows if order is important.

Run Configuration Options:
    1. gcp_project_id: Required
        - A valid GCP project ID which will be used for querying the LLM

    2. gcp_project_location: Required
        - A valid region from where the vertexAI resources will be utilized

    3. model: Required
        - An LLM model to use for evaluation. e.g. gemini-1.5-pro-002
"""

from typing import Tuple
import json
import logging

from scorers import comparator
import vertexai
from vertexai.preview.generative_models import GenerationConfig, GenerativeModel


class VertexMatcher(comparator.Comparator):
    """
    VertexMatcher class implements the Comparator base class.

    Attributes:
        1. name: Name of the comparator. Set to "vertexmatcher"
        2. config: Scorer config defined in the run config yaml file
        3. gcp_project_id: ID of the GCP project ID from where the resources will be utilized
        4. gcp_project_location: A valid region for vertexAI resources
        5. model: The LLM model to use
    """

    def __init__(self, config: dict):
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
        query_type: str,
        golden_execution_result: str,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: str,
        generated_eval_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        if golden_error:
            return 0, "Golden query failed to execute."
        if generated_error:
            return 0, "Generated query failed to execute."

        only_first_n = 50
        
        golden_execution_result = comparator.take_n_uniques(
            golden_execution_result, only_first_n
        )
        generated_execution_result = comparator.take_n_uniques(
            generated_execution_result, only_first_n
        )

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
