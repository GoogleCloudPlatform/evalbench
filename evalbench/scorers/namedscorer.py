"""NamedScorer class to wrap any base comparator with a custom metric name."""

from typing import Any, Tuple
from scorers import comparator


class NamedScorer(comparator.Comparator):
    """Wraps an underlying base comparator with a custom metric name."""

    def __init__(
        self,
        name: str,
        base_scorer: comparator.Comparator,
        target_type: str | None = None
    ):
        super().__init__({})
        self.base_scorer = base_scorer
        self.target_type = target_type

        base_name = getattr(base_scorer, "name", "")
        if target_type and base_name.startswith(target_type):
            suffix = base_name[len(target_type):]
            self.name = f"{name}{suffix}"
        elif "_" in base_name and base_name.rsplit("_", 1)[1].isdigit():
            suffix = f"_{base_name.rsplit('_', 1)[1]}"
            self.name = f"{name}{suffix}"
        else:
            self.name = name

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
        database: str = "",
        **kwargs,
    ) -> Tuple[float, str] | list[Tuple[str, float | None, str]]:
        """Delegate comparison to the underlying base scorer."""
        import inspect
        sig = inspect.signature(self.base_scorer.compare)
        call_kwargs = {}
        if "database" in sig.parameters and database:
            call_kwargs["database"] = database
        for k, v in kwargs.items():
            if k in sig.parameters:
                call_kwargs[k] = v

        res = self.base_scorer.compare(
            nl_prompt,
            golden_query,
            query_type,
            golden_execution_result,
            golden_eval_result,
            golden_error,
            generated_query,
            generated_execution_result,
            generated_eval_result,
            generated_error,
            **call_kwargs,
        )
        if isinstance(res, list):
            new_list = []
            base_name = getattr(self.base_scorer, "name", "")
            for item in res:
                row_name = item[0]
                rest = item[1:]
                if self.target_type and row_name.startswith(self.target_type):
                    suffix = row_name[len(self.target_type):]
                    new_name = f"{self.name}{suffix}"
                elif base_name and row_name.startswith(base_name):
                    suffix = row_name[len(base_name):]
                    new_name = f"{self.name}{suffix}"
                elif row_name == base_name:
                    new_name = self.name
                else:
                    new_name = row_name
                new_list.append((new_name, *rest))
            return new_list
        return res
