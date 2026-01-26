"""
TrajectoryMatcher

It compares the expected tool usage trajectory with the actual executed tools.
"""

from typing import Tuple, Any
from scorers import comparator


class TrajectoryMatcher(comparator.Comparator):
    """
    TrajectoryMatcher class implements the Comparator base class for checking tool execution trajectories.

    It checks if the sequence of executed tools matches the expected trajectory.
    """

    def __init__(self, config: dict):
        self.name = "trajectory_matcher"
        self.config = config
        self.ignore_order = config.get("ignore_order", False)
        self.allow_extras = config.get("allow_extras", False)

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: list,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: list,
        generated_eval_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        """
        Compares expected trajectory (golden) with actual executed tools (generated).

        Args:
            golden_execution_result: List of expected tool names (strings).
            generated_execution_result: List of actually executed tool names (strings).

        Returns:
            Tuple (score, explanation)
        """
        if generated_error:
            return 0.0, f"Generation error: {generated_error}"

        expected = golden_execution_result or []
        actual = generated_execution_result or []

        if not isinstance(expected, list) or not isinstance(actual, list):
            return 0.0, "Trajectory data must be lists."

        if self.ignore_order:
            # Set comparison
            if self.allow_extras:
                # Subset check (original logic)
                match = set(expected).issubset(set(actual))
                explanation = "All expected tools were called (order ignored, extras allowed)." if match else f"Missing tools: {set(expected) - set(actual)}"
            else:
                # Exact set match
                match = set(expected) == set(actual)
                explanation = "Tool sets match exactly." if match else f"Set mismatch. Expected: {set(expected)}, Actual: {set(actual)}"
        else:
            # Ordered comparison
            if self.allow_extras:
                match = False
                explanation = "Not implemented."

            # Strict match (default)
            match = expected == actual
            explanation = "Trajectories match exactly." if match else f"Trajectory mismatch. Expected: {expected}, Actual: {actual}"

        score = 100.0 if match else 0.0
        return score, explanation
