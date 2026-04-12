"""
ToolCallLatency Scorer

Measures the total time spent executing tool subprocesses.
"""
from typing import Tuple, Any
from scorers import comparator
import json


class ToolCallLatency(comparator.Comparator):
    """
    ToolCallLatency class implements the Comparator base class for checking
    tool execution duration.
    """

    def __init__(self, config: dict):
        self.name = "tool_call_latency"
        self.config = config

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
        Calculates tool call latency from the conversation history.

        Args:
            generated_eval_result: String representing JSON history.


        Returns:
            Tuple (score, explanation) where score is the duration in ms.
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

            total_duration = 0.0

            if isinstance(history, list):
                for turn in history:
                    agent_resp = turn.get("agent", "")
                    try:
                        resp_json = json.loads(agent_resp)
                        stats = resp_json.get("stats", {})
                        tools = stats.get("tools", {})
                        total_duration += tools.get("totalDurationMs", 0.0)
                    except json.JSONDecodeError:
                        pass

                return float(total_duration), (
                    f"Tool calls took {total_duration} ms."
                )
            else:
                return 0.0, "Conversation history is not a list."
        except json.JSONDecodeError:
            return 0.0, "Failed to parse conversation history as JSON."
