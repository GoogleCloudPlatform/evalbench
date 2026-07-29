"""Trajectory-coverage scorer: how many of the product's tools any CUJ exercises.

Static (no judge). Tools referenced in a trajectory but absent from the schema
don't count toward coverage (that staleness is golden-validation's concern), so
the score is capped by the real catalog.

Scope: the MCP tool channel only. Skills (expected_skills) have no static catalog
to score against, so skill coverage is a separate follow-up.
"""

import logging

from scorers.dataset_quality.context import (
    CATEGORY_TOOL_ACTIVATION,
    DatasetQualityContext,
    SubScoreContribution,
    SubScorer,
)


class TrajectoryCoverageScorer(SubScorer):
    """Fraction of schema tools exercised by at least one expected_trajectory."""

    name = "trajectory_coverage"
    category = CATEGORY_TOOL_ACTIVATION
    default_weight = 20

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        schema_tools = set(context.tool_names)
        if not schema_tools:
            return SubScoreContribution(applicable=False)

        trajectory_tools = set()
        for scenario in context.scenarios:
            trajectory_tools.update(scenario.get("expected_trajectory") or [])

        covered = schema_tools & trajectory_tools
        uncovered = sorted(schema_tools - covered)
        score = round(len(covered) / len(schema_tools) * 100, 2)

        suggestions = []
        if uncovered:
            suggestions.append(
                "No CUJ exercises these tools: " + ", ".join(uncovered)
            )
        logging.info(
            "trajectory_coverage: \t%d/%d tools covered -> %.2f",
            len(covered), len(schema_tools), score,
        )
        return SubScoreContribution(
            score=score,
            metrics={
                "dq_covered_tools": len(covered),
                "dq_total_tools": len(schema_tools),
            },
            suggestions=suggestions,
        )
