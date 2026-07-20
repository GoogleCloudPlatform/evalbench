"""
AgentSteps Scorer

Measures the agent's actual work across the conversation: the total number of
tool-call round trips the agent made to complete the task. Unlike turn_count
(which counts user<->agent conversation rounds), this reflects the internal
effort collapsed inside each agent reply.

A "step" is a single tool call. Per turn, the count is taken from the agent
payload's ``stats.tools.totalCalls`` when available, falling back to the length
of the ``tool_calls`` list. Turns whose agent reply is plain text (or otherwise
carries no parseable tool information) contribute zero steps.
"""
from typing import Tuple, Any
from scorers import comparator
import json


class AgentSteps(comparator.Comparator):
    """
    AgentSteps implements the Comparator base class for counting the agent's
    tool-call round trips across a conversation.
    """

    def __init__(self, config: dict):
        self.name = "agent_steps"
        self.config = config

    def _count_steps(self, agent_resp: Any) -> int | None:
        """Returns tool-call count for one agent reply, or None if unparseable."""
        payload = agent_resp
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(payload, dict):
            return None

        stats = payload.get("stats", {})
        tools = stats.get("tools", {}) if isinstance(stats, dict) else {}
        if isinstance(tools, dict) and "totalCalls" in tools:
            try:
                return int(tools["totalCalls"])
            except (TypeError, ValueError):
                pass

        tool_calls = payload.get("tool_calls")
        if isinstance(tool_calls, list):
            return len(tool_calls)

        # Parseable payload with no tool information: zero steps this turn.
        return 0

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: Any,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: Any,
        generated_eval_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        """
        Sums tool-call round trips from the conversation history.

        Args:
            generated_eval_result: String representing JSON
            conversation history.

        Returns:
            Tuple (score, explanation) where score is the total tool-call count.
        """
        if generated_error:
            return 0.0, f"Generation error: {generated_error}"

        if not generated_eval_result:
            return 0.0, "No conversation history provided."

        try:
            history = (
                json.loads(generated_eval_result)
                if isinstance(generated_eval_result, str)
                else generated_eval_result
            )
            if isinstance(history, dict):
                history = history.get("conversation_history", "[]")
            if isinstance(history, str):
                history = json.loads(history)

            if not isinstance(history, list):
                return 0.0, "Conversation history is not a list."

            total_steps = 0
            skipped = 0
            for entry in history:
                if not isinstance(entry, dict):
                    skipped += 1
                    continue
                steps = self._count_steps(entry.get("agent", ""))
                if steps is None:
                    skipped += 1
                    continue
                total_steps += steps

            skipped_note = (
                f" Skipped {skipped} turn(s) with unparseable agent payloads."
                if skipped
                else ""
            )
            return float(total_steps), (
                f"Agent made {total_steps} tool-call step(s).{skipped_note}"
            )
        except json.JSONDecodeError:
            return 0.0, "Failed to parse conversation history as JSON."
