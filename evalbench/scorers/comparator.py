"""Comparator base class."""

import abc
import dataclasses
import datetime
import decimal
import json
import logging
import traceback
from typing import Any, Tuple


class Comparator(abc.ABC):
    """Base class for comparators."""

    def __init__(self, config: dict):
        """Initializes the Comparator with a config.

        Args:
          name: A descriptive name for the comparison strategy.
        """
        self.name = "base"

    @abc.abstractmethod
    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        golden_execution_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_error: Any,
    ) -> Tuple[float, str]:
        """Abstract method to compare two execution results.

        Subclasses must implement this method to provide specific comparison logic.

        Args:
          golden_query: The golden query from the eval set.
          golden_execution_result: The expected execution result.
          generated_query: The generated query.
          generated_execution_result: The actual execution result, obtained by
            running the generated query.

        Returns:
          Tuple[int, str] containing a score and an analysis of the comparison.
        """
        raise NotImplementedError("Subclasses must implement this method")


@dataclasses.dataclass
class ComparisonResult:
    """Represents the result of a comparison operation.

    Attributes:
        comparator (Comparator): The Comparator instance used for the comparison.
        comparison_error (Optional[Exception]): Exception object if an error
          occurred during the comparison. Defaults to None.
        comparison_logs (str): The logs of the comparison. Defaults to None.
        score (int): The score of the comparison, ranging from 0 to 100.
    """

    def __init__(
        self,
        comparator: Comparator,
        score: int,
        comparison_logs: str | None = None,
        comparison_error: Exception | None = None,
    ):
        """Initializes a ComparisonResult instance with the provided comparator, score, optional error object."""

        self.comparator = comparator
        self.comparison_error = comparison_error
        self.comparison_logs = comparison_logs
        self.score = score

    def to_dict(self) -> dict:
        return {
            "comparator": self.comparator.name,
            "score": self.score,
            "comparison_error": self.comparison_error,
            "comparison_logs": self.comparison_logs
        }


def convert_to_hashable(obj: Any) -> Any | None:
    """convert_to_hashable.

    Args:
      obj:

    Returns:
    """
    if isinstance(obj, dict):
        # Sort the dictionary by keys to ensure consistent string representation
        sorted_dict = {
            key: convert_to_hashable(value) for key, value in sorted(obj.items())
        }
        return json.dumps(sorted_dict, sort_keys=True)
    elif isinstance(obj, (list, tuple)):
        return tuple(convert_to_hashable(item) for item in obj)
    elif isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, decimal.Decimal):
        return float(obj)
    else:
        return obj


def process_list(items: list[Any]) -> list[Any] | None:
    """process_list.

    Args:
      items:

    Returns:
    """
    hashable_items = [convert_to_hashable(item) for item in items]
    unique_items = list(set(hashable_items))
    return sorted(unique_items)


def convert_to_set(results: list[Any]) -> list[Any] | None:
    """convert_to_set.

    Args:
      results:

    Returns:
    """
    try:
        if isinstance(results, list):
            return results
        # trying to not process unless required to avoid File too large error as
        # results set are too large specially for air_travel.
        results = list(set(results))
    except TypeError:
        print(f'Error converting results to set {traceback.format_exc()}')
        logging.exception('Call to "list" resulted in an error')
        # Convert to hashable items as we cannot create set out of non-hashable
        # values, remove duplicates & sort.
        results = process_list(results)
    return results
