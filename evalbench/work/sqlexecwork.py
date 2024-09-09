"""Work is the base class for all work items."""

from typing import Any
from work import Work


class SQLExecWork(Work):
    """SQLExecWork Generates SQL from the generator."""

    def __init__(self, db: Any,  eval_result: dict):
        self.db = db
        self.eval_result = eval_result

    def run(self, work_config: str = None) -> dict:
        """Runs the work item.

        Args:
          work_config:

        Returns:

        """
        generated_result = None
        generated_error = None
        golden_result = None
        golden_error = None

        if self.eval_result["sql_generator_error"] is None:
            self.eval_result["sanitized_sql"] = (
                self.eval_result["generated_sql"]
                .replace('sql: "', "")
                .replace('\\n', " ")
                .replace("\\n", " ")
                .replace("\\", "")
                .replace("  ", "")
                .replace("`", "")
            )
            generated_result, generated_error = self.db.execute(self.eval_result["sanitized_sql"])
            golden_sql = ""
            if isinstance(self.eval_result["golden_sql"], str):
                golden_sql = self.eval_result["golden_sql"]
            elif isinstance(self.eval_result["golden_sql"], list) and len(self.eval_result["golden_sql"]) > 0:
                golden_sql = self.eval_result["golden_sql"][0]
            golden_result, golden_error = self.db.execute(golden_sql)

        self.eval_result["generated_result"] = generated_result
        self.eval_result["generated_error"] = generated_error

        self.eval_result["golden_result"] = golden_result
        self.eval_result["golden_error"] = golden_error
        return self.eval_result
