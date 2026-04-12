"""
TurnCount Scorer

Measures the number of round trips (turns) the agent takes to complete a task.
"""
from typing import Tuple, Any
from scorers import comparator
import json


class TurnCount(comparator.Comparator):
    """
    TurnCount class implements the Comparator base class for checking
    tool execution turns.
    """

    def __init__(self, config: dict):
        self.name = "turn_count"
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
        Calculates turn count from the conversation history.

        Args:
            generated_eval_result: String representing JSON
            conversation history.

        Returns:
            Tuple (score, explanation) where score is the number of turns.
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

            if isinstance(history, list):
                turns = len(history)
                return float(turns), f"Agent took {turns} turns."
            else:
                return 0.0, "Conversation history is not a list."
        except json.JSONDecodeError:
            return 0.0, "Failed to parse conversation history as JSON."
