"""Work is the base class for all work items."""

from typing import Any


class Work:
    """Work is the base class for all work items."""

    def __init__(self, item: Any):
        self.item = item

    def run(self, work_config: Any = None) -> str | dict:
        """Runs the work item.

        Args:
          work_config:

        Returns:

        """
        return f"{self.item} {work_config}"
