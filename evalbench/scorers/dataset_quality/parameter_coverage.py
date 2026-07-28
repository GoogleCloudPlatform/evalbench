"""Parameter-coverage scorer: how much of the tool param surface CUJs exercise.

A judge decides, per ``(tool, parameter)``, which CUJs would force the agent to
supply a value for it (CUJs carry no literal argument values, so this is inferred
from the scenario text). A parameter is covered when at least ``min_items`` CUJs
exercise it. The denominator comes from the tool schema, not the judge, so a
parameter the judge omits counts as uncovered.
"""

import logging
from collections import Counter, defaultdict

from scorers.dataset_quality.context import (
    CATEGORY_DISCOVERABILITY,
    DEFAULT_CUJ_FIELDS,
    DatasetQualityContext,
    JudgeSubScorer,
    SubScoreContribution,
)
from scorers.dataset_quality.llm import judge_coverage
from scorers.dataset_quality.prompts.parameter_coverage import (
    PARAMETER_COVERAGE_PROMPT,
    PARAMETER_COVERAGE_SCHEMA,
)


class ParameterCoverageScorer(JudgeSubScorer):
    """Fraction of schema parameters exercised by at least ``min_items`` CUJs."""

    name = "parameter_coverage"
    category = CATEGORY_DISCOVERABILITY
    default_weight = 13

    def __init__(self, config: dict, global_models):
        super().__init__(config, global_models)
        self.min_items = int((config or {}).get("min_items", 1))

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False)

        params = context.tool_parameters()
        if not params:
            return SubScoreContribution(applicable=False)

        prompt = PARAMETER_COVERAGE_PROMPT.format(
            tool_schema=context.tool_schema_json(),
            cujs_json=context.cujs_json(DEFAULT_CUJ_FIELDS),
        )
        valid_ids = set(context.cuj_ids)

        coverage = judge_coverage(
            self.model, prompt, response_schema=PARAMETER_COVERAGE_SCHEMA
        )
        if coverage is None:
            return SubScoreContribution(applicable=False)

        counts: dict[tuple[str, str], set] = {}
        for entry in coverage:
            key = (entry.get("tool"), entry.get("parameter"))
            if key not in params:
                continue
            ids = entry.get("cuj_ids") or []
            counts.setdefault(key, set()).update(
                i for i in ids if i in valid_ids
            )

        covered = {k for k in params if len(counts.get(k, set())) >= self.min_items}
        uncovered = sorted(params - covered)
        score = round(len(covered) / len(params) * 100, 2)

        suggestions = []
        if uncovered:
            covered_by_tool = Counter(tool for tool, _ in covered)
            total_by_tool = Counter(tool for tool, _ in params)
            uncovered_by_tool: dict[str, list[str]] = defaultdict(list)
            for tool, param in uncovered:
                uncovered_by_tool[tool].append(param)
            groups = [
                f"{tool} ({covered_by_tool[tool]}/{total_by_tool[tool]} covered): "
                + ", ".join(uncovered_by_tool[tool])
                for tool in sorted(uncovered_by_tool)
            ]
            suggestions.append(
                f"No CUJ (>= {self.min_items}) exercises these parameters, "
                "by tool: " + "; ".join(groups)
            )
        logging.info(
            "parameter_coverage: \t%d/%d params covered -> %.2f",
            len(covered), len(params), score,
        )
        return SubScoreContribution(
            score=score,
            metrics={
                "dq_param_covered": len(covered),
                "dq_param_total": len(params),
            },
            suggestions=suggestions,
        )
