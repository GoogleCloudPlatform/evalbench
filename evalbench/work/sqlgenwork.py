"""Work is the base class for all work items."""

from typing import Any
from work import Work
import time


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
        generator_result = None
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
                    start_time = time.time()
                    generator_result = self.generator.generate(
                        self.eval_result["generated_prompt"]
                    )
                    end_time = time.time()
                    self.eval_result["sql_generator_time"] = end_time - start_time
                except Exception as e:
                    sql_generator_error = str(e)

        if generator_result is not None:
            generated_sql = self._get_generated_sql(generator_result)
            self._attach_generator_info(self.eval_result, generator_result)

        self.eval_result["generated_sql"] = generated_sql
        self.eval_result["sql_generator_error"] = sql_generator_error
        return self.eval_result

    def _get_generated_sql(self, generator_result: Any) -> Any:
        if isinstance(generator_result, dict):
            return generator_result.get("generated_sql")
        return generator_result

    def _attach_generator_info(self, eval_result: dict, generator_result: Any) -> None:
        if isinstance(generator_result, dict):
            eval_result.update(generator_result)
