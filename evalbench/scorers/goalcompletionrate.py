from typing import Tuple, Any
import logging
from scorers import comparator
from generators.models import get_generator
from .prompt.goalcompletion import GOAL_COMPLETION_PROMPT
from scorers.util import format_conversation_history
import json


class GoalCompletionRate(comparator.Comparator):
    """
    Evaluates whether the agent accomplished the conversation plan's intent.
    """

    def __init__(self, config: dict, global_models):
        self.name = "goal_completion"
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for GoalCompletionRate")
        self.model = get_generator(global_models, self.model_config)
        self.include_tool_calls = config.get("include_tool_calls", False)

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
        conversation_plan = scenario.get("conversation_plan", "")

        formatted_history = format_conversation_history(
            conversation_history, include_tool_calls=self.include_tool_calls
        )

        prompt = GOAL_COMPLETION_PROMPT.format(
            conversation_plan=conversation_plan,
            conversation_history=formatted_history
        )

        try:
            response = self.model.generate(prompt)
            response_text = getattr(
                response, 'stdout', response) if response else ""
            if isinstance(response_text, str):
                first_line = response_text.strip().split('\n')[0].upper()
                score = 100.0 if "PASS" in first_line else 0.0
                return score, response_text
            return 0.0, "Failed to parse LLM evaluation response."
        except Exception as e:
            logging.error(f'GoalCompletionRate generation failed: {e}')
            return 0.0, f"Error calling model: {e}"

