from typing import Tuple, Any
import logging
from scorers import comparator
from generators.models import get_generator
from .prompt.parameteranalysis import PARAMETER_ANALYSIS_PROMPT
import json


class ParameterAnalysis(comparator.Comparator):
    """
    Evaluates tool execution parameters and provides qualitative feedback.
    """

    def __init__(self, config: dict, global_models):
        self.name = "parameter_analysis"
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for ParameterAnalysis")
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
            return 100.0, (
                "No eval result context passed. Parameter analysis skipped."
            )

        try:
            context = (
                json.loads(generated_eval_result)
                if isinstance(generated_eval_result, str)
                else generated_eval_result
            )
        except json.JSONDecodeError:
            return 100.0, "Invalid JSON in eval result context."

        conversation_history = context.get("conversation_history", "[]")
        accumulated_tools = context.get("accumulated_tools", [])

        prompt = PARAMETER_ANALYSIS_PROMPT.format(
            accumulated_tools=json.dumps(accumulated_tools, indent=2),
            conversation_history=conversation_history
        )

        try:
            response = self.model.generate(prompt)
            response_text = getattr(
                response, 'stdout', response) if response else ""
            if isinstance(response_text, str):
                return 100.0, response_text

            return 0.0, "Failed to parse LLM evaluation response."
        except Exception as e:
            logging.error(f'ParameterAnalysis generation failed: {e}')
            return 0.0, f"Error calling model: {e}"
