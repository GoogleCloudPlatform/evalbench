"""MultiTrialComparator base class."""

import abc
from typing import Any, Tuple


class MultiTrialComparator(abc.ABC):
    """Base class for multi-trial comparators."""

    def __init__(self, config: dict):
        """Initializes the MultiTrialComparator with a config.

        Args:
          config: The scorer config.
        """
        self.name = "base_multi_trial"
        self.config = config

    @abc.abstractmethod
    def compare(
        self,
        nl_prompt: str,
        trial1_query: str,
        trial1_execution_result: list,
        trial1_eval_result: str,
        trial1_error: str,
        trial2_query: str,
        trial2_execution_result: list,
        trial2_eval_result: str,
        trial2_error: str,
    ) -> Tuple[float, str]:
        """Abstract method to compare two trials.

        Subclasses must implement this method to provide specific comparison logic.

        Args:
          nl_prompt: The natural language prompt.
          trial1_query: The generated query for trial 1.
          trial1_execution_result: The execution result for trial 1.
          trial1_eval_result: The eval result for trial 1 (optional).
          trial1_error: The error for trial 1 (optional).
          trial2_query: The generated query for trial 2.
          trial2_execution_result: The execution result for trial 2.
          trial2_eval_result: The eval result for trial 2 (optional).
          trial2_error: The error for trial 2 (optional).

        Returns:
          Tuple[float, str] containing a score and an analysis of the comparison.
        """
        raise NotImplementedError("Subclasses must implement this method")
