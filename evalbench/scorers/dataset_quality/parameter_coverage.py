"""Parameter-coverage scorer: how much of the tool param surface CUJs exercise.

A judge decides, per ``(tool, parameter)``, which CUJs would force the agent to
supply a value for it (CUJs carry no literal argument values, so this is inferred
from the scenario text). A parameter is covered when at least one CUJ exercises
it. The denominator comes from the tool schema, not the judge, so a parameter the
judge omits counts as uncovered.

Scored over tools named by at least one ``expected_trajectory``. Parameters of a
tool no CUJ invokes can only ever be uncovered, so grading them here would
restate trajectory_coverage's tool gap once per parameter instead of measuring
how deeply the dataset drives the tools it does reach.
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
from scorers.dataset_quality.llm import example_prompts, judge_coverage
from scorers.dataset_quality.prompts.parameter_coverage import (
    PARAMETER_COVERAGE_PROMPT,
    PARAMETER_COVERAGE_SCHEMA,
    RECOMMENDATIONS_KEY,
)


class ParameterCoverageScorer(JudgeSubScorer):
    """Fraction of schema parameters exercised by at least one CUJ."""

    name = "parameter_coverage"
    category = CATEGORY_DISCOVERABILITY
    default_weight = 13

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False)

        tools = context.exercised_tools()
        params = context.tool_parameters(tools)
        if not params:
            return SubScoreContribution(applicable=False)

        prompt = PARAMETER_COVERAGE_PROMPT.format(
            tool_schema=context.tool_schema_json(tools),
            cujs_json=context.cujs_json(DEFAULT_CUJ_FIELDS),
        )
        valid_ids = set(context.cuj_ids)

        data = judge_coverage(
            self.model, prompt, response_schema=PARAMETER_COVERAGE_SCHEMA
        )
        if data is None:
            return SubScoreContribution(applicable=False)

        counts: dict[tuple[str, str], set] = {}
        for entry in data["coverage"]:
            key = (entry.get("tool"), entry.get("parameter"))
            if key not in params:
                continue
            ids = entry.get("cuj_ids") or []
            counts.setdefault(key, set()).update(
                i for i in ids if i in valid_ids
            )

        covered = {k for k in params if counts.get(k)}
        uncovered = sorted(params - covered)
        score = round(len(covered) / len(params) * 100)

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
                f"Over the {len(tools)} of {len(context.tool_names)} schema tools"
                " some CUJ invokes, no CUJ exercises these parameters, by tool: "
                + "; ".join(groups)
            )
        logging.info(
            "parameter_coverage: \t%d/%d params over %d exercised tools -> %d",
            len(covered), len(params), len(tools), score,
        )
        return SubScoreContribution(
            score=score,
            metrics={
                "params_covered": len(covered),
                "params_in_scope": len(params),
            },
            suggestions=suggestions,
            example_prompts=(
                example_prompts(data, RECOMMENDATIONS_KEY) if uncovered else []
            ),
        )
