"""Work is the base class for all work items."""

from typing import Any
from work import Work


class DataAgentVUserWork(Work):
    """DataAgentVUserWork runs disambiguation."""

    def __init__(self, vuser: Any, eval_result: dict):
        self.vuser = vuser
        self.eval_result = eval_result

    def run(self, work_config: str = None) -> dict:
        """Runs the work item.

        Args:
          work_config:

        Returns:

        """
        vuser_error = None
        generated = None
        if self.eval_result["sql_generator_error"] is None:
            try:
                generated = self.vuser.disambiguate(self.eval_result)
                self.eval_result["generated"] = generated
            except Exception as e:
                vuser_error = str(e)
        self.eval_result["vuser_error"] = vuser_error
        return self.eval_result
