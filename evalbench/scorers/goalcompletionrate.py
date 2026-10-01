from typing import Tuple, Any
import logging
from scorers import comparator
from generators.models import get_generator
from .prompt.goalcompletion import GOAL_COMPLETION_PROMPT
import json


def _is_met(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return value is True


class GoalCompletionRate(comparator.Comparator):
    """
    Evaluates whether the agent accomplished the conversation plan's intent.

    The judge splits the plan into sub-goals and rules on each; the score is
    the share met.
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

        from .util import extract_json, filter_conversation_history_json

        history_list = context.get("conversation_history", [])
        formatted_history = filter_conversation_history_json(
            history_list, include_tool_calls=self.include_tool_calls
        )

        scenario = context.get("scenario", {})
        conversation_plan = scenario.get("conversation_plan", "")

        prompt = GOAL_COMPLETION_PROMPT.format(
            conversation_plan=conversation_plan,
            conversation_history=formatted_history
        )

        try:
            response = self.model.generate(prompt)
            response_text = getattr(
                response, 'stdout', response) if response else ""
            if not isinstance(response_text, str):
                return 0.0, "Failed to parse LLM evaluation response."

            try:
                data = extract_json(response_text)
            except ValueError as e:
                logging.error(f'GoalCompletionRate response unparsable: {e}')
                return 0.0, f"Invalid JSON in response: {response_text[:200]}"

            sub_goals = [
                sg for sg in (data.get("sub_goals") or [])
                if isinstance(sg, dict)
            ]
            if not sub_goals:
                return 0.0, f"No sub-goals returned: {response_text[:200]}"

            met_count = sum(1 for sg in sub_goals if _is_met(sg.get("met")))
            score = round(100.0 * met_count / len(sub_goals), 1)

            lines = [f"{met_count}/{len(sub_goals)} sub-goals met."]
            for sg in sub_goals:
                status = "MET" if _is_met(sg.get("met")) else "UNMET"
                lines.append(
                    f"[{status}] {sg.get('goal', '')} — {sg.get('reasoning', '')}"
                )
            summary = data.get("summary", "")
            if summary:
                lines.append(f"Summary: {summary}")
            return score, "\n".join(lines)
        except Exception as e:
            logging.error(f'GoalCompletionRate generation failed: {e}')
            return 0.0, f"Error calling model: {e}"
