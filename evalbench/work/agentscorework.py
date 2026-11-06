"""AgentScoreWork class."""

from typing import Any
from work.work import Work
from scorers import score as scorer
import threading


class AgentScoreWork(Work):
    """Work class for scoring agent generation results."""

    def __init__(
        self,
        config: dict,
        eval_output: dict,
        scoring_results: list,
        global_models: Any = None,
    ):
        self.config = config
        self.eval_output = eval_output
        self.scoring_results = scoring_results

        if global_models is None:
            global_models = {
                "lock": threading.Lock(),
                "semaphores": {},
                "registered_models": {}
            }
        self.global_models = global_models

    def run(self, work_config: Any = None) -> Any:
        """Runs the agent scoring work.

        Args:
            work_config: Optional configuration for the work.

        Returns:
            The scoring result dictionary.
        """
        scenario = self.eval_output.get("scenario", {})
        metadata = self.eval_output.get("metadata", {})

        scoring_item = {
            "id": self.eval_output.get("eval_id"),
            "nl_prompt": scenario.get("starting_prompt", ""),
            "golden_sql": "",
            "query_type": "",
            "golden_result": scenario.get("expected_trajectory", []),
            "golden_eval_results": "",
            "golden_error": "",
            "generated_sql": "skipped",
            "generated_result": self.eval_output.get("accumulated_tools", []),
            "eval_results": self.eval_output,
            "generated_error": None,
            "dialects": metadata.get("dialects", []),
            "database": metadata.get("database", "unknown"),
            "job_id": self.eval_output.get("job_id"),
        }

        first_row = len(self.scoring_results)
        scorer.compare(
            eval_output_item=scoring_item,
            experiment_config=self.config,
            scoring_results=self.scoring_results,
            global_models=self.global_models
        )
        # Label the new score rows with their trial.
        if "trial_index" in self.eval_output:
            for row in self.scoring_results[first_row:]:
                row["prompt_id"] = self.eval_output["prompt_id"]
                row["trial_index"] = self.eval_output["trial_index"]

        return self.eval_output
