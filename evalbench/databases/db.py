from abc import ABC, abstractmethod
from typing import Any, Tuple


class DB(ABC):

    def __init__(self, db_config):
        pass

    @abstractmethod
    def generate_schema(self):
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def execute(self, query: str, use_cache=False) -> Tuple[Any, Any]:
        """Execute a query string and return the execution result and the error (or None if no error).
        Args:
            query (str): The SQL query to execute.
            use_cache (bool): Whether to use cache for DB execution result.

        Returns:
            Tuple[Any, Any]: The result of the query execution and the error (or None if no error).
        """
        raise NotImplementedError("Subclasses must implement this method")
