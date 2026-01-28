"""AgentScoreWork class."""

from typing import Any
from work.work import Work
from scorers import score as scorer
from dataset.evaloutput import EvalOutput


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
            "eval_results": "",
            "generated_error": None,
            "dialects": metadata.get("dialects", []),
            "database": metadata.get("database", "unknown"),
            "job_id": self.eval_output.get("job_id"),
        }

        scorer.compare(
            eval_output_item=scoring_item,
            experiment_config=self.config,
            scoring_results=self.scoring_results,
            global_models=self.global_models
        )

        return self.eval_output
