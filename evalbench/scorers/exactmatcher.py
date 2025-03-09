"""
ExactMatcher

It is a simple comparison strategy which checks if expected and generated results are exactly the same.

Note: Since this is a list comparison, ordering of the results matter here.

Run Configuration Options:
    1. use_eval_sql: Optional
        - Allowed values: True, False
        - Default value: False
        - When use_eval_sql is set to True, it compares the results of eval queries which are used to verify DDL and DML queries
"""

from typing import Tuple

from scorers import comparator


class ExactMatcher(comparator.Comparator):
    """
    ExactMatcher class implements the Comparator base class with exact match logic.

    Attributes:
      1. name: Name of the comparator. Set to "exact match"
      2. config: the scorer config defined in the run config yaml file
    """

    def __init__(self, config: dict):
        self.name = "exact_match"
        self.config = config

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
        """compare function implements the comparison logic for ExactMatcher comparator."""

        if golden_error or generated_error:
            return 0, None
        if self.config and "use_eval_sql" in self.config:
            score = 100 if golden_eval_result == generated_eval_result else 0
            return score, None
        else:
            score = 100 if golden_execution_result == generated_execution_result else 0
            return score, None
