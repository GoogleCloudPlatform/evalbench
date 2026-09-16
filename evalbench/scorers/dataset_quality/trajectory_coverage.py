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


def _describe(name: str, description: str) -> str:
    """A gap entry, carrying the skill's description when the catalog has one.

    The synthesis pass may only reason from what the report states, so a bare
    name would limit a recommendation to restating the name.
    """
    # Frontmatter descriptions are routinely multi-line YAML blocks, but the gap
    # list is one comma-joined line.
    description = " ".join(description.split())
    if not description:
        return name
    if len(description) > _DESCRIPTION_CHARS:
        description = description[:_DESCRIPTION_CHARS].rstrip() + "..."
    return f"{name} ({description})"


class TrajectoryCoverageScorer(SubScorer):
    """Fraction of the product's capability catalog that some CUJ exercises."""

    name = "trajectory_coverage"
    category = CATEGORY_TOOL_ACTIVATION
    default_weight = 20

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        if context.skills_error:
            # Tools alone are a smaller denominator, which reads higher than truth.
            logging.warning(
                "trajectory_coverage: skipped; skills unresolved (%s)",
                context.skills_error,
            )
            return SubScoreContribution(applicable=False)

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
                "No CUJ exercises these tools or scripts: "
                + ", ".join(sorted(operations - covered_operations))
            )
        if skills - covered_skills:
            descriptions = {
                skill.name: skill.description for skill in context.skills
            }
            suggestions.append(
                "No CUJ exercises these skills: "
                + ", ".join(
                    _describe(name, descriptions.get(name, ""))
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
                "capabilities_covered": covered,
                "capabilities_total": total,
            },
            suggestions=suggestions,
        )
