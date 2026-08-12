"""Shared interface for the plug-and-play dataset-quality scorers.

The orchestrator loads a product's CUJ scenarios and tool schema once, then hands
every configured scorer the same :class:`DatasetQualityContext` and collects each
scorer's :class:`SubScoreContribution`. A new scorer plugs in by subclassing
:class:`SubScorer` (or :class:`JudgeSubScorer`), registering its class in the
orchestrator's registry, and adding a block under ``sub_scorers:`` in the run
config.

Kept free of concrete-scorer imports so both the scorers and the orchestrator can
import it without a cycle.
"""

from dataclasses import dataclass, field
import json
from typing import Any

from generators.models import get_generator


# Score categories the sub-scorers roll up into. Single source of truth so a
# scorer's ``category`` and the grading rollup can't desync on a typo.
CATEGORY_TOOL_ACTIVATION = "tool_activation_faithfulness"
CATEGORY_DISCOVERABILITY = "discoverability_coverage"
CATEGORY_ERROR_RECOVERY = "error_recovery_coverage"
CATEGORY_COMPOSITION = "composition_coverage"
CATEGORY_DIVERSITY = "cuj_diversity"

# The CUJ fields most judge scorers project into their prompt. vague_examples and
# naming_distribution intentionally use a narrower set and pass their own.
DEFAULT_CUJ_FIELDS = [
    "starting_prompt",
    "conversation_plan",
    "expected_trajectory",
]


def expected_trajectory(scenario: dict) -> list:
    """A scenario's ``expected_trajectory``, or empty when it isn't a list.

    A bare string would otherwise iterate per character into the tool sets, which
    scores silently wrong rather than failing.
    """
    trajectory = scenario.get("expected_trajectory")
    return trajectory if isinstance(trajectory, list) else []


def expected_skills(scenario: dict) -> list:
    """A scenario's expected_skills, or empty when it isn't a list."""
    skills = scenario.get("expected_skills")
    return skills if isinstance(skills, list) else []


@dataclass
class DatasetQualityContext:
    """Everything a scorer needs to grade one product's CUJ dataset."""

    product_name: str
    scenarios: list[dict]
    tools: list  # list[mcp.types.Tool]
    skills: list = field(default_factory=list)  # list[skills_catalog.Skill]

    @property
    def n(self) -> int:
        return len(self.scenarios)

    @property
    def cuj_ids(self) -> list[str]:
        return [scenario.get("id") for scenario in self.scenarios]

    @property
    def tool_names(self) -> list[str]:
        names = (self._tool_field(tool, "name") for tool in self.tools)
        return [name for name in names if name]

    @property
    def skill_names(self) -> list[str]:
        return list(dict.fromkeys(skill.name for skill in self.skills if skill.name))

    @property
    def script_names(self) -> list[str]:
        """Every script shipped by a catalog skill, de-duplicated across skills."""
        scripts = []
        for skill in self.skills:
            scripts.extend(skill.scripts)
        return list(dict.fromkeys(scripts))

    @property
    def tool_names_str(self) -> str:
        return ", ".join(self.tool_names) or "(none provided)"

    @staticmethod
    def _tool_field(tool, key: str):
        value = getattr(tool, key, None)
        if value is None and isinstance(tool, dict):
            value = tool.get(key)
        return value

    def exercised_tools(self) -> list:
        """Schema tools named by at least one CUJ's ``expected_trajectory``.

        A tool no trajectory names has no reachable parameters, so any scorer
        grading against it would restate trajectory_coverage's tool gap.
        """
        named = set()
        for scenario in self.scenarios:
            named.update(expected_trajectory(scenario))
        return [
            tool for tool in self.tools
            if self._tool_field(tool, "name") in named
        ]

    def tool_parameters(self, tools) -> set[tuple[str, str]]:
        """Every named parameter of ``tools`` as a ``(tool_name, parameter)`` pair."""
        params = set()
        for tool in tools:
            name = self._tool_field(tool, "name")
            schema = self._tool_field(tool, "inputSchema") or {}
            for param in (schema.get("properties") or {}):
                params.add((name, param))
        return params

    def tool_catalog_json(self) -> str:
        """JSON string of the tool catalog (name + description per tool)."""
        tools = [
            {
                "name": self._tool_field(tool, "name"),
                "description": self._tool_field(tool, "description") or "",
            }
            for tool in self.tools
        ]
        return json.dumps({"tools": tools}, indent=2, default=str)

    def tool_schema_json(self, tools) -> str:
        """JSON string of ``tools`` (name + inputSchema per tool)."""
        entries = []
        for tool in tools:
            schema = self._tool_field(tool, "inputSchema") or {}
            entries.append({
                "name": self._tool_field(tool, "name"),
                "inputSchema": {
                    "properties": schema.get("properties") or {},
                    "required": schema.get("required") or [],
                },
            })
        return json.dumps({"tools": entries}, indent=2, default=str)

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
    - ``metrics``: raw counters behind the score, merged into the category's
      metrics and cited by the synthesis pass.
    - ``suggestions``: human-readable improvement notes (non-weighted).
    - ``example_prompts``: example starting prompts for CUJs the dataset is
      missing. Not rendered directly; the synthesis pass turns them into
      recommendations, so they are stated once rather than alongside a
      near-identical gap.
    - ``evidence``: per-CUJ classifier tags grouped by label (which CUJ ids got
      which classification), surfaced for the UI and to ground the synthesis pass.
    - ``distribution``: dataset-wide descriptive counts keyed by the report field
      they belong under (e.g. ``{"cuj_path_distribution": {...}}``). The
      orchestrator hoists these to the report's top level instead of nesting them
      under the scorer's category, since they describe the whole dataset.
    """

    score: float | None = None
    applicable: bool = True
    metrics: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    example_prompts: list[str] = field(default_factory=list)
    evidence: dict[str, list[str]] = field(default_factory=dict)
    distribution: dict[str, Any] = field(default_factory=dict)


class SubScorer:
    """Base for dataset-quality sub-scorers.

    Subclasses set ``name`` (matching its key under ``sub_scorers:``, and also its
    comparator name), ``category``, ``default_weight``, and implement
    ``run(context) -> SubScoreContribution``.
    """

    name: str
    category: str
    default_weight: float

    def __init__(self, config: dict, global_models):
        self.weight = float((config or {}).get("weight", self.default_weight))

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        raise NotImplementedError


class JudgeSubScorer(SubScorer):
    """Base for sub-scorers that grade via an LLM judge."""

    def __init__(self, config: dict, global_models):
        super().__init__(config, global_models)
        model_config = (config or {}).get("model_config")
        if not model_config:
            raise ValueError(f"model_config is required for the {self.name} scorer")
        self.model = get_generator(global_models, model_config)
