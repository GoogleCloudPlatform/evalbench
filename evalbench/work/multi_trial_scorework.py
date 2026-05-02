"""MultiTrialScorerWork is the class for all multi-trial scoring work."""

from typing import Any
from scorers import multi_trial_score
from work import Work


class MultiTrialScorerWork(Work):
    """MultiTrialScorerWork is the class for all multi-trial scoring work."""

    def __init__(
        self,
        prompt_id: str,
        nl_prompt: str,
        trials: list,
        experiment_config: dict,
        multi_trial_scoring_results: list,
        global_models,
        progress_reporting=None,
    ):
        self.prompt_id = prompt_id
        self.nl_prompt = nl_prompt
        self.trials = trials
        self.experiment_config = experiment_config
        self.multi_trial_scoring_results = multi_trial_scoring_results
        self.global_models = global_models
        self.progress_reporting = progress_reporting

    def run(self, work_config: Any = None) -> list:
        """Score the multi-trial work item.

        Args:
          work_config:

        Returns:
          List of pairwise scoring results.
        """
        results = multi_trial_score.compare(
            self.prompt_id,
            self.nl_prompt,
            self.trials,
            self.experiment_config,
            self.global_models,
        )
        self.multi_trial_scoring_results.extend(results)

        if self.progress_reporting:
            from evaluator.progress_reporter import record_successful_scoring

            record_successful_scoring(self.progress_reporting)

        return results
