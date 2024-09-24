"""Work is the base class for all work items."""

from typing import Any
from work import Work


class SQLGenWork(Work):
    """SQLGenWork Generates SQL from the generator."""

    def __init__(self, generator: Any, eval_result: dict):
        self.generator = generator
        self.eval_result = eval_result

    def run(self, work_config: str = None) -> dict:
        """Runs the work item.

        Args:
          work_config:

        Returns:

        """
        generated_sql = None
        sql_generator_error = None
        if self.eval_result["prompt_generator_error"] is None:
            if "noop" in self.generator.name:
                # only set these if value is truthy, to avoid issues like
                # proto default value empty string false positive error.
                if self.eval_result["generated_sql"]:
                    generated_sql = self.eval_result["generated_sql"]
                if self.eval_result["sql_generator_error"]:
                    sql_generator_error = self.eval_result["sql_generator_error"]
            else:
                try:
                    generated_sql = self.generator.generate(
                        self.eval_result["generated_prompt"]
                    )
                except Exception as e:
                    sql_generator_error = str(e)

        self.eval_result["generated_sql"] = generated_sql
        self.eval_result["sql_generator_error"] = sql_generator_error
        return self.eval_result
