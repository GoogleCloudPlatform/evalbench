"""Trajectory-coverage scorer: how many of the product's tools any CUJ exercises.

Static (no judge). Collects every tool named across all ``expected_trajectory``
lists and measures what share of the product's tool schema those trajectories
cover. A dataset that never exercises a tool can't tell you whether that tool is
discoverable or works -- low coverage means blind spots. Tools referenced in a
trajectory but absent from the schema don't count toward coverage (that staleness
is golden-validation's concern), so the score is capped by the real catalog.

Scope: the MCP tool channel only. Skills (expected_skills) are not measured here
-- there's no static skill catalog to score against (skills install from repos and
are only named post-install), so skill coverage is a separate follow-up.
"""

import logging

from scorers.dataset_quality.context import (
    CATEGORY_TOOL_ACTIVATION,
    DatasetQualityContext,
    SubScoreContribution,
)


class TrajectoryCoverageScorer:
    """Fraction of schema tools exercised by at least one expected_trajectory."""

    category = CATEGORY_TOOL_ACTIVATION

    def __init__(self, config: dict, global_models):
        self.name = "trajectory_coverage"
        config = config or {}
        self.weight = float(config.get("weight", 12))

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        schema_tools = set(context.tool_names)
        if not schema_tools:
            return SubScoreContribution(applicable=False, logs="no tools in schema")

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
            row_fields={
                "dq_covered_tools": len(covered),
                "dq_total_tools": len(schema_tools),
            },
            suggestions=suggestions,
            logs=f"covered={len(covered)}/{len(schema_tools)}",
        )
