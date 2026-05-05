from typing import Tuple, Any
import logging
from scorers import comparator
from generators.models import get_generator
from scorers.prompt.binaryrubricscorer import BINARY_RUBRIC_EVAL_PROMPT
import re
import json


class BinaryRubricScorer(comparator.Comparator):
    """
    Evaluates whether the agent satisfied specific rubric criteria
    with a binary pass/fail.
    """

    def __init__(self, config: dict, global_models,
                 criterion: str = "", index: int = 0):
        self.name = f"binary_rubric_scorer_{index}"
        self.criterion = criterion
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for BinaryRubricScorer")
        self.model = get_generator(global_models, self.model_config)

    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
        generated_error: Any,
    ) -> Tuple[float, str]:

        if not generated_eval_result:
            return 0.0, "No eval result context passed."

        try:
            context = (
                json.loads(generated_eval_result)
                if isinstance(generated_eval_result, str)
                else generated_eval_result
            )
        except json.JSONDecodeError:
            return 0.0, "Invalid JSON in eval result context."

        conversation_history = context.get("conversation_history", "[]")
        scenario = context.get("scenario", {})
        criterion_to_evaluate = self.criterion
        if not criterion_to_evaluate:
            scenario_rubrics = scenario.get("binary_rubric", [])
            if scenario_rubrics and scenario_rubrics[0]:
                criterion_to_evaluate = scenario_rubrics[0]

        if not criterion_to_evaluate:
            return 100.0, (
                "No rubric defined for this scenario. Defaulting to PASS."
            )

        prompt = BINARY_RUBRIC_EVAL_PROMPT.format(
            rubric_item=criterion_to_evaluate,
            conversation_history=conversation_history
        )

        try:
            response = self.model.generate(prompt)
            response_text = getattr(
                response, 'stdout', response) if response else ""
            if isinstance(response_text, str):
                first_line = response_text.strip().split('\n')[0]
                match = re.search(r'(\d+)/1', first_line)
                if match:
                    score = 100.0 if int(match.group(1)) >= 1 else 0.0
                    return score, response_text

                # Fallback to PASS/FAIL check
                score = 100.0 if "PASS" in first_line.upper() else 0.0
                return score, response_text
            return 0.0, "Failed to parse LLM evaluation response."

        except Exception as e:
            logging.error(f'BinaryRubricScorer generation failed: {e}')
            return 0.0, f"Error calling model: {e}"
