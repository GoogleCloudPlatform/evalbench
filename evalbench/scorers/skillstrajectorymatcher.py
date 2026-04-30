"""
SkillsTrajectoryMatcher

Compares expected activated skills vs actual activated skill names,
using the same Jaccard/Levenshtein approach as TrajectoryMatcher.
"""
from typing import Tuple, Any, List
from scorers import comparator
import json


class SkillsTrajectoryMatcher(comparator.Comparator):
    """
    Compares expected_skills from scenario with accumulated_skills from execution.

    Reads golden/generated data from the eval_results context dict because
    skills data does not map to the generic golden_result/generated_result fields
    used by trajectory_matcher for tools.
    """

    def __init__(self, config: dict):
        self.name = "skills_trajectory"
        self.config = config
        self.enforce_order = config.get("enforce_order", False)

    def _levenshtein_distance(self, seq1: List[str], seq2: List[str]) -> int:
        n, m = len(seq1), len(seq2)
        if n == 0:
            return m
        if m == 0:
            return n

        dp = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n + 1):
            dp[i][0] = i
        for j in range(m + 1):
            dp[0][j] = j

        for i in range(1, n + 1):
            for j in range(1, m + 1):
                cost = 0 if seq1[i - 1] == seq2[j - 1] else 1
                dp[i][j] = min(
                    dp[i - 1][j] + 1,
                    dp[i][j - 1] + 1,
                    dp[i - 1][j - 1] + cost
                )

        return dp[n][m]

    def _jaccard_similarity(self, set1: set, set2: set) -> float:
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return 1.0 if union == 0 else intersection / union

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
        if generated_error:
            return 0.0, f"Generation error: {generated_error}"

        # Extract context from eval_results (same pattern as ParameterAnalysis)
        try:
            context = (
                json.loads(generated_eval_result)
                if isinstance(generated_eval_result, str)
                else generated_eval_result
            )
        except (json.JSONDecodeError, TypeError):
            return 0.0, "Invalid or missing eval result context."

        scenario = context.get("scenario", {})
        expected = scenario.get("expected_skills", []) or []
        actual = context.get("accumulated_skills", []) or []

        if not isinstance(expected, list) or not isinstance(actual, list):
            return 0.0, "Skills data must be lists."

        if not expected and not actual:
            return 100.0, "Both expected and actual skill lists are empty."

        if self.enforce_order:
            distance = self._levenshtein_distance(expected, actual)
            max_len = max(len(expected), len(actual))
            normalized_score = max(
                0.0, 1.0 - (distance / max_len)) if max_len > 0 else 1.0
            score = normalized_score * 100.0
            return score, (
                f"Skills Sequence Alignment: {score:.2f} "
                f"(Distance: {distance}, Max: {max_len}). "
                f"Expected: {expected}, Actual: {actual}"
            )
        else:
            similarity = self._jaccard_similarity(set(expected), set(actual))
            score = similarity * 100.0
            return score, (
                f"Skills Jaccard Similarity: {score:.2f}. "
                f"Expected: {set(expected)}, Actual: {set(actual)}"
            )
