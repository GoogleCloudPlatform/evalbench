from typing import Tuple, Any
import logging
from scorers import comparator
from generators.models import get_generator
from .prompt.behavioralmetrics import BEHAVIORAL_METRICS_PROMPT
import json
import re


class BehavioralMetrics(comparator.Comparator):
    """
    Evaluates both Hallucination Rate and Clarification Rate
    in a single LLM pass.
    """

    def __init__(self, config: dict, global_models):
        self.name = "behavioral_metrics"
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for BehavioralMetrics")
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
        conversation_plan = scenario.get("conversation_plan", "")

        prompt = BEHAVIORAL_METRICS_PROMPT.format(
            conversation_plan=conversation_plan,
            conversation_history=conversation_history
        )

        try:
            response = self.model.generate(prompt)
            response_text = getattr(
                response, 'stdout', response) if response else ""
            if isinstance(response_text, str):
                hallucinations = 0
                clarifications = 0

                hallucination_match = re.search(
                    r'Hallucination Count:\s*(\\d+)', response_text)
                if hallucination_match:
                    hallucinations = int(hallucination_match.group(1))

                clarification_match = re.search(
                    r'Clarification Count:\s*(\\d+)', response_text)
                if clarification_match:
                    clarifications = int(clarification_match.group(1))

                # Baseline score starts at 100 and drops per hallucination
                # (e.g. 50pts) and clarification (e.g. 20pts)
                penalty = (hallucinations * 50) + (clarifications * 20)
                score = max(0.0, 100.0 - penalty)
                return score, response_text

            return 0.0, "Failed to parse LLM evaluation response."
        except Exception as e:
            logging.error(f'BehavioralMetrics generation failed: {e}')
            return 0.0, f"Error calling model: {e}"
