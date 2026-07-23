"""Shared interface for the plug-and-play dataset-quality scorers.

The orchestrator loads a product's CUJ scenarios and tool schema once, then hands
every configured scorer the same :class:`DatasetQualityContext` and collects each
scorer's :class:`SubScoreContribution`. A new scorer plugs in by registering its
class in the orchestrator's registry and adding a block under ``scorers:`` in the
run config.

Each dataset_quality scorer implements:
  - ``name``: str, matches its key under ``scorers:`` (also its comparator).
  - ``weight``: float, its share of the weighted global score.
  - ``run(context) -> SubScoreContribution``: grade one product's dataset.

Kept free of concrete-scorer imports so both the scorers and the orchestrator can
import it without a cycle.
"""

from dataclasses import dataclass, field
import json
from typing import Any


# Score categories the sub-scorers roll up into. Single source of truth so a
# scorer's ``category`` and the grading rollup can't desync on a typo.
CATEGORY_TOOL_ACTIVATION = "tool_activation_faithfulness"
CATEGORY_DISCOVERABILITY = "discoverability_coverage"
CATEGORY_ERROR_RECOVERY = "error_recovery_coverage"
CATEGORY_COMPOSITION = "composition_coverage"


@dataclass
class DatasetQualityContext:
    """Everything a scorer needs to grade one product's CUJ dataset."""

    product_name: str
    scenarios: list[dict]
    tools: list  # list[mcp.types.Tool]

    @property
    def n(self) -> int:
        return len(self.scenarios)

    @property
    def tool_names(self) -> list[str]:
        names = []
        for tool in self.tools:
            name = getattr(tool, "name", None)
            if name is None and isinstance(tool, dict):
                name = tool.get("name")
            if name:
                names.append(name)
        return names

    @property
    def tool_names_str(self) -> str:
        return ", ".join(self.tool_names) or "(none provided)"

    @staticmethod
    def _tool_field(tool, key: str):
        value = getattr(tool, key, None)
        if value is None and isinstance(tool, dict):
            value = tool.get(key)
        return value

    def tool_parameters(self) -> set[tuple[str, str]]:
        """Every named parameter as a ``(tool_name, parameter)`` pair."""
        params = set()
        for tool in self.tools:
            name = self._tool_field(tool, "name")
            schema = self._tool_field(tool, "inputSchema") or {}
            for param in (schema.get("properties") or {}):
                params.add((name, param))
        return params

    def tool_schema_json(self) -> str:
        """JSON string of the tool schema (name + inputSchema per tool)."""
        tools = []
        for tool in self.tools:
            schema = self._tool_field(tool, "inputSchema") or {}
            tools.append({
                "name": self._tool_field(tool, "name"),
                "inputSchema": {
                    "properties": schema.get("properties") or {},
                    "required": schema.get("required") or [],
                },
            })
        return json.dumps({"tools": tools}, indent=2, default=str)

    def cujs_json(self, fields: list[str]) -> str:
        """JSON string of each scenario projected to ``id`` + ``fields``.

        Only the fields a given prompt needs are included, so unrelated scenario
        keys (env, kind, ...) don't leak into the judge input.
        """
        projected = []
        for scenario in self.scenarios:
            obj = {"id": scenario.get("id")}
            for field_name in fields:
                if field_name in scenario:
                    obj[field_name] = scenario.get(field_name)
            projected.append(obj)
        return json.dumps(projected, indent=2, default=str)


@dataclass
class SubScoreContribution:
    """What a scorer returns for one product.

    - ``score``: 0-100, or ``None`` when the scorer could not produce a number.
    - ``applicable``: False drops the scorer from the weighted global score
      (e.g. an empty dataset or a metric that doesn't apply to this product).
    - ``row_fields``: extra detail columns merged into the product's BQ row.
    - ``suggestions``: human-readable improvement notes (non-weighted).
    - ``evidence``: per-CUJ classifier tags grouped by label (which CUJ ids got
      which classification), surfaced for the UI and to ground the synthesis pass.
    - ``logs``: short human-readable summary for logging.
    """

    score: float | None = None
    applicable: bool = True
    row_fields: dict[str, Any] = field(default_factory=dict)
    suggestions: list = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    logs: str = ""
