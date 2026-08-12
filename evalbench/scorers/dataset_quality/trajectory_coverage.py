"""Trajectory-coverage scorer: how much of a product's surface any CUJ exercises.

The catalog is every channel a product declares at once — MCP tools, the scripts
its skills ship, and the skills themselves — so a dataset written against one
channel isn't graded as though the others didn't exist.

Names match verbatim, so one operation exposed through two channels is two
entries. A name no channel declares is ignored rather than flagged; stale
trajectories are golden-validation's concern.
"""

import logging

from scorers.dataset_quality.context import (
    CATEGORY_TOOL_ACTIVATION,
    DatasetQualityContext,
    SubScoreContribution,
    SubScorer,
    expected_skills,
    expected_trajectory,
)


# Enough of a skill's description to say what a missing CUJ would cover, without
# a long gap list becoming a wall of text in the rendered report.
_DESCRIPTION_CHARS = 120


class TrajectoryCoverageScorer(SubScorer):
    """Fraction of the product's capability catalog that some CUJ exercises."""

    name = "trajectory_coverage"
    category = CATEGORY_TOOL_ACTIVATION
    default_weight = 30

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        operations = set(context.tool_names) | set(context.script_names)
        skills = set(context.skill_names)
        total = len(operations) + len(skills)
        if not total:
            return SubScoreContribution(applicable=False)

        named_operations = set()
        named_skills = set()
        for scenario in context.scenarios:
            named_operations.update(expected_trajectory(scenario))
            named_skills.update(expected_skills(scenario))

        covered_operations = operations & named_operations
        covered_skills = skills & named_skills
        covered = len(covered_operations) + len(covered_skills)
        score = round(covered / total * 100)

        suggestions = []
        if operations - covered_operations:
            suggestions.append(
                "No CUJ exercises these operations: "
                + ", ".join(sorted(operations - covered_operations))
            )
        if skills - covered_skills:
            suggestions.append(
                "No CUJ exercises these skills: "
                + ", ".join(
                    self._describe(context, name)
                    for name in sorted(skills - covered_skills)
                )
            )
        logging.info(
            "trajectory_coverage: \t%d/%d covered (%d/%d operations, %d/%d "
            "skills) -> %d",
            covered, total,
            len(covered_operations), len(operations),
            len(covered_skills), len(skills),
            score,
        )
        return SubScoreContribution(
            score=score,
            metrics={
                "dq_covered_tools": covered,
                "dq_total_tools": total,
            },
            suggestions=suggestions,
        )

    @staticmethod
    def _describe(context: DatasetQualityContext, name: str) -> str:
        """A gap entry, carrying the skill's description when the catalog has one.

        The synthesis pass may only reason from what the report states, so a bare
        name would limit a recommendation to restating the name.
        """
        for skill in context.skills:
            if skill.name == name and skill.description:
                description = skill.description[:_DESCRIPTION_CHARS].rstrip()
                return f"{name} ({description})"
        return name
