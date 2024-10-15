from scorers import comparator
from typing import Tuple


class ReturnedSQL(comparator.Comparator):
    """ReturnedSQL scorer checks if the generated SQL query contains anything except comments.

    It assigns a score of 100 if there are non-comment lines, otherwise a score of 0.
    """

    def __init__(self, config: dict):
        self.name = "returned_sql"
        self.config = config

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        golden_execution_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:

        if generated_query == "":
            return 100, None

        query_lines = [line.strip() for line in generated_query.splitlines()]
        has_non_comment_line = any(line and not line.startswith("--") for line in query_lines)

        score = 100 if has_non_comment_line else 0
        return score, None
