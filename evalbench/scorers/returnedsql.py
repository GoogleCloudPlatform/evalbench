"""
ReturnedSQL

This comparison stragtegy checks if the generated sql query has any non-comment lines.
It assigns a score of 100 if there are non-comment lines, otherwise a score of 0.

It checks for both single line comments (start with --) and multiline comments (start with /*, end with */)

Runtime Options: None
"""

from scorers import comparator
from typing import Tuple


class ReturnedSQL(comparator.Comparator):
    """
    ReturnedSQL class implements the Comparator base class with SQL finding logic.

    Attributes:
        1. name: Name of the comparator. Set to "returned_sql"
        2. config: Scorer config defined in the run config yaml file
    """

    def __init__(self, config: dict):
        self.name = "returned_sql"
        self.config = config

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: str,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: str,
        generated_eval_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        """compare function implements the SQL finding logic for ReturnedSQL comparator."""

        if generated_query == "":
            return 100, None

        query_lines = [line.strip() for line in generated_query.splitlines()]
        has_non_comment_line = any(
            line and (not line.startswith("--") and not line.startswith("/*")) for line in query_lines
        )

        score = 100 if has_non_comment_line else 0
        return score, None
