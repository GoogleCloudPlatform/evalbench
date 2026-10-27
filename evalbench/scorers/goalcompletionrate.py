from typing import Tuple, Any
import logging
from scorers import comparator
from generators.models import get_generator
from util.rate_limit import rate_limit
from .prompt.goalcompletion import (
    GOAL_COMPLETION_SCHEMA,
    build_goal_completion_prompt,
)
from .util import (
    extract_json,
    filter_conversation_history_json,
    generate_json,
    supports_json_mode,
)
import json


def _is_met(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return value is True


def _records_tool_calls(history: Any) -> bool:
    """Returns True if any agent turn in `history` has a `tool_calls` list.

    Some harnesses, such as agy_cli, do not record tool calls.
    """
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except (json.JSONDecodeError, TypeError):
            return False
    for turn in history if isinstance(history, list) else []:
        agent = turn.get("agent") if isinstance(turn, dict) else None
        if isinstance(agent, str):
            try:
                agent = json.loads(agent)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(agent, dict) and isinstance(agent.get("tool_calls"), list):
            return True
    return False


class GoalCompletionRate(comparator.Comparator):
    """
    Evaluates whether the agent accomplished the conversation plan's intent.

    The judge splits `conversation_plan` into sub-goals and rules on each one.
    The score is the share of sub-goals the agent met.
    """

    def __init__(self, config: dict, global_models):
        self.name = "goal_completion"
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for GoalCompletionRate")
        self.model = get_generator(global_models, self.model_config)
        self.include_tool_calls = config.get("include_tool_calls", False)

    def _generate(self, prompt: str, schema: dict) -> str:
        """Returns the judge's raw reply.

        Uses Gemini JSON mode (`generate_json`) if the judge supports it and
        has no tools, because JSON mode cannot call tools. Otherwise uses
        `model.generate`. JSON mode uses temperature 0. `model.generate` uses
        the judge model's default temperature.
        """
        model = self.model
        if (not supports_json_mode(model)
                or getattr(model, "_genai_tool_config", None)):
            response = model.generate(prompt)
            return getattr(response, "stdout", response) if response else ""
        # generate_json skips QueryGenerator.generate, so apply its rate limit.
        # Some judges cap output below 65536 and reject a higher limit, so use
        # the model's default.
        return rate_limit(
            (getattr(model, "base_prompt", "") + prompt, schema),
            lambda p, s: generate_json(model, p, s, max_output_tokens=None),
            model.execs_per_minute,
            model.semaphore,
            model.max_attempts,
        )

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

        history_list = context.get("conversation_history", [])
        formatted_history = filter_conversation_history_json(
            history_list, include_tool_calls=self.include_tool_calls
        )

        scenario = context.get("scenario", {})
        conversation_plan = scenario.get("conversation_plan", "")
        if not str(conversation_plan or "").strip():
            return 0.0, "The scenario has no conversation_plan to grade."

        prompt = build_goal_completion_prompt(
            conversation_plan=conversation_plan,
            conversation_history=formatted_history,
            # With no tool calls to check, the judge must use the agent's report.
            include_tool_calls=(self.include_tool_calls
                                and _records_tool_calls(history_list)),
        )

        # A judge error raises, so score.py records it in comparison_error and
        # the viewer shows it. It must not look like a judged score of 0.
        response_text = self._generate(prompt, GOAL_COMPLETION_SCHEMA)
        if not response_text:
            raise RuntimeError(
                "goal_completion judge returned an empty response (see logs)")
        if not isinstance(response_text, str):
            return 0.0, "Failed to parse LLM evaluation response."

        try:
            data = extract_json(response_text)
        except ValueError as e:
            logging.error(f'GoalCompletionRate response unparsable: {e}')
            return 0.0, f"Invalid JSON in response: {response_text[:200]}"

        goals = [
            (sg.get("goal", ""), _is_met(sg.get("met")),
             sg.get("reasoning", ""))
            for sg in (data.get("sub_goals") or [])
            if isinstance(sg, dict)
        ]
        if not goals:
            return 0.0, f"No sub-goals returned: {response_text[:200]}"

        met_count = sum(1 for _, met, _ in goals if met)
        score = round(100.0 * met_count / len(goals), 1)

        lines = [f"{met_count}/{len(goals)} sub-goals met."]
        for goal, met, reasoning in goals:
            status = "MET" if met else "UNMET"
            lines.append(f"[{status}] {goal} — {reasoning}")

        summary = data.get("summary", "")
        if summary:
            lines.append(f"Summary: {summary}")
        return score, "\n".join(lines)
