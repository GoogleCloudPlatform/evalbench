from typing import Tuple, Any
import logging
from scorers import comparator
from generators.models import get_generator
from .prompt.goalcompletion import build_goal_completion_prompt
import json


def _is_met(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return value is True


def _criteria_list(value: Any) -> list[str]:
    """Returns the non-blank items of the optional `success_criteria` field.

    A bare string counts as one criterion. Whitespace inside a criterion
    collapses to single spaces, so each criterion fits on one prompt line.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        if value is not None:
            logging.warning(
                f"Ignored success_criteria that is not a list: {value!r}")
        return []
    criteria = []
    for item in value:
        if isinstance(item, str) and item.strip():
            criteria.append(" ".join(item.split()))
        elif item is not None:
            logging.warning(
                f"Ignored success criterion that is not a string: {item!r}")
    return criteria


def _match_verdicts(items: Any, count: int) -> Tuple[list, list]:
    """Pairs the judge's verdicts with the criteria by 1-based `index`.

    A verdict without an `index` takes its position in the list. If two
    verdicts share an index, the first one is used.

    Returns:
      The verdicts in criteria order, and the 1-based numbers of the criteria
      that have no verdict.
    """
    by_index = {}
    for position, item in enumerate(items if isinstance(items, list) else [], 1):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index", position))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= count:
            by_index.setdefault(index, item)
    missing = [i for i in range(1, count + 1) if i not in by_index]
    return [by_index.get(i) for i in range(1, count + 1)], missing


class GoalCompletionRate(comparator.Comparator):
    """
    Evaluates whether the agent accomplished the conversation plan's intent.

    The score is the share of goals the agent met. By default, the judge splits
    `conversation_plan` into sub-goals. If the scenario sets the optional
    `success_criteria` list, the judge rules on those criteria instead.
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
        success_criteria = _criteria_list(scenario.get("success_criteria"))

        prompt = build_goal_completion_prompt(
            conversation_plan=conversation_plan,
            conversation_history=formatted_history,
            success_criteria=success_criteria,
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

            if success_criteria:
                verdicts, missing = _match_verdicts(
                    data.get("success_criteria"), len(success_criteria))
                if missing:
                    return 0.0, (
                        f"No verdict for success criteria {missing}: "
                        f"{response_text[:200]}")
                goals = [
                    (criterion, _is_met(v.get("met")), v.get("reasoning", ""))
                    for criterion, v in zip(success_criteria, verdicts)
                ]
                label = "success criteria"
            else:
                goals = [
                    (sg.get("goal", ""), _is_met(sg.get("met")),
                     sg.get("reasoning", ""))
                    for sg in (data.get("sub_goals") or [])
                    if isinstance(sg, dict)
                ]
                if not goals:
                    return 0.0, f"No sub-goals returned: {response_text[:200]}"
                label = "sub-goals"

            met_count = sum(1 for _, met, _ in goals if met)
            score = round(100.0 * met_count / len(goals), 1)

            lines = [f"{met_count}/{len(goals)} {label} met."]
            for goal, met, reasoning in goals:
                status = "MET" if met else "UNMET"
                lines.append(f"[{status}] {goal} — {reasoning}")

            summary = data.get("summary", "")
            if summary:
                lines.append(f"Summary: {summary}")
            return score, "\n".join(lines)
        except Exception as e:
            logging.error(f'GoalCompletionRate generation failed: {e}')
            return 0.0, f"Error calling model: {e}"
