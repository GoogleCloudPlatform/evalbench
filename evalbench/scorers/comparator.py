"""Comparator base class."""

import abc
import dataclasses
import datetime
import decimal
import json
from typing import Any, Tuple

from .util import make_hashable


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
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
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
            "comparison_logs": self.comparison_logs,
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


def convert_to_set(results: list[dict]):
    """
    Converts a list of dictionaries to a set of tuples sorted according to their key
    Two dictionaries are considered the same if all their key-value pairs are the same.
    Dictionaries can contain lists as values.

    Args:
        1. results - The list of dictionaries.

    Returns:
        1. results_set - A set of sorted tuples
    """

    def make_hashable(item):
        """Recursively converts items to hashable types (tuples)."""
        if isinstance(item, list):
            return tuple(make_hashable(x) for x in item)
        elif isinstance(item, dict):
            return tuple(sorted((k, make_hashable(v)) for k, v in item.items()))
        else:
            return item

    results_set = {make_hashable(d) for d in results}
    return results_set

def take_n_uniques(output_list: list, n: int) -> list:
    """Takes n number of unique (non duplicate) values from the output list.

    Args:
        output_list: The execution output result set
        n: Max number of unique values needed.

    Returns:
        The execution output result set without duplicates in a size of n values or less.
    """
    seen_dicts = set()
    new_list = []
    for d in output_list:
        # Convert the dictionary to a hashable frozenset for efficient lookup
        t = frozenset((k, make_hashable(v)) for k, v in d.items())
        if t not in seen_dicts:
            seen_dicts.add(t)
            new_list.append(d)
            if len(new_list) == n:
                break
    return new_list
