"""Work is the base class for all work items."""

from typing import Any
from work import Work


class SQLGenQueryDataWork(Work):
    """SQLGenQueryDataWork Generates SQL from the generator."""

    def __init__(self, generator: Any, eval_result: dict):
        self.generator = generator
        self.eval_result = eval_result

    def run(self, work_config: str = None) -> dict:
        """Runs the work item.

        Args:
          work_config:

        Returns:

        """
        sql_generator_error = None
        if self.eval_result["prompt_generator_error"] is None:
            try:
                result = self.generator.generate(self.eval_result)
                if not isinstance(result, str):
                    self.eval_result = result
                else:
                    self.eval_result["generated_sql"] = None
                    self.eval_result["sql_generator_error"] = "No result generated"
            except Exception as e:
                import traceback

                traceback.print_exc()
                sql_generator_error = str(e)

        self.eval_result["sql_generator_error"] = sql_generator_error
        return self.eval_result
