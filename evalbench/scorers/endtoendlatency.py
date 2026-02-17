"""
EndToEndLatency Scorer

Measures the total latency per successful user journey.
"""
from typing import Tuple, Any
from scorers import comparator
import json


class EndToEndLatency(comparator.Comparator):
    """
    EndToEndLatency class implements the Comparator base class for checking
    overall latency.
    """

    def __init__(self, config: dict):
        self.name = "end_to_end_latency"
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
        Calculates end-to-end latency from the conversation history.

        Args:
            generated_eval_result: String holding JSON
            history.

        Returns:
            Tuple (score, explanation) for duration in ms.
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

                        tools_stats = stats.get("tools", {})
                        td = tools_stats.get("totalDurationMs", 0.0)
                        total_duration += td

                        models = stats.get("models", {})
                        for model_stats in models.values():
                            api_stats = model_stats.get("api", {})
                            a = api_stats.get("totalLatencyMs", 0.0)
                            total_duration += a

                    except json.JSONDecodeError:
                        pass

                return float(total_duration), (
                    f"End-to-End latency was {total_duration} ms."
                )
            else:
                return 0.0, "Conversation history is not a list."
        except json.JSONDecodeError:
            return 0.0, "Failed to parse conversation history as JSON."
