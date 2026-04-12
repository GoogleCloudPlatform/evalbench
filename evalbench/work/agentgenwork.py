"""AgentGenWork class."""

from typing import Any, Callable
import json
import logging

from work.work import Work


class AgentGenWork(Work):
    """Work class for running agent generation and scoring."""

    def __init__(
        self,
        processor: Callable,
        eval_result: Any,
        job_id: str = "",
        metadata: dict = None,
        simulated_user: Any = None
    ):
        self.processor = processor
        self.eval_result = eval_result
        self.job_id = job_id
        self.metadata = metadata or {}
        self.simulated_user = simulated_user

    def run(self, work_config: Any = None) -> Any:
        """Runs the agent generation and scoring work.

        Args:
            work_config: Optional configuration for the work.

        Returns:
            The updated eval_result with results and scores.
        """
        eval_result = self.eval_result
        if not hasattr(eval_result, "agent_results"):
            eval_result.agent_results = []
        if not hasattr(eval_result, "scoring_results"):
            eval_result.scoring_results = []

        try:
            eval_set = json.loads(eval_result.payload)
            for scenario in eval_set.get("scenarios", []):
                self.processor(
                    scenario,
                    self.eval_result,
                    self.job_id,
                    self.metadata,
                    self.simulated_user
                )
        except Exception as e:
            logging.error(f"Error processing item: {e}")

        return eval_result
