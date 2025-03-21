"""
This comparison strategy compares the set of generated and expected results and gives full score if the sets match exactly.
This is the execution accuracy measured in BIRD

Run configurations: None
"""

from typing import Tuple

from scorers import comparator
from scorers.comparator import convert_to_set


class SetMatcher(comparator.Comparator):
    """
    SetMatcher class implements the Comparator base class with set comparison logic.

    Attributes:
        1. name: Name of the comparator. Set to "set_match"
        2. config: Scorer config defined in the run config yaml file
    """

    def __init__(self, config: dict):
        self.name = "set_match"
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
        """Implements the set comparison logic"""

        if golden_error or generated_error:
            return 0, None
        else:
            # Current results are a list of Dict. Converting to Tuple for set comparison
            golden_execution_result_tuple = [
                tuple(d.values()) for d in golden_execution_result
            ]
            generated_execution_result_tuple = [
                tuple(d.values()) for d in generated_execution_result
            ]
            score = (
                100
                if set(golden_execution_result_tuple)
                == set(generated_execution_result_tuple)
                else 0
            )

            return score, None
