"""ExactMatchConsistencyComparator."""

from typing import Tuple
from scorers import multi_trial_comparator


class ExactMatchConsistencyComparator(
    multi_trial_comparator.MultiTrialComparator
):
    """ExactMatchConsistencyComparator implements the MultiTrialComparator base class with exact match logic.

    Attributes:
      name: Name of the comparator. Set to "exact_match_consistency"
      config: the scorer config
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "exact_match_consistency"

    def compare(
        self,
        nl_prompt: str,
        trial1_query: str,
        trial1_execution_result: list,
        trial1_eval_result: str,
        trial1_error: str,
        trial2_query: str,
        trial2_execution_result: list,
        trial2_eval_result: str,
        trial2_error: str,
    ) -> Tuple[float, str]:
        """compare function implements the comparison logic for ExactMatchConsistencyComparator."""

        if trial1_error or trial2_error:
            if trial1_error == trial2_error:
                return 100.0, f"Both trials failed with the same error: {trial1_error}"
            else:
                return (
                    0.0,
                    (
                        f"Inconsistent errors. Trial 1: {trial1_error}, Trial 2:"
                        f" {trial2_error}"
                    ),
                )

        # if eval_result is present, use it to compare rather than execution
        if trial1_eval_result:
            score = 100.0 if trial1_eval_result == trial2_eval_result else 0.0
            return score, "Used eval_result to score."
        else:
            score = (
                100.0 if trial1_execution_result == trial2_execution_result else 0.0
            )
            return score, ""
