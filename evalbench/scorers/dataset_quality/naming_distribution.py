"""Naming-distribution scorer: how many CUJs name a tool instead of intent.

Static (no judge). A discoverable product is one a user can drive by describing
their goal ("which databases cost the most?"), not by naming the operation
("run list_instances"). This counts CUJs whose user-facing text echoes a tool's
name and scores the *indirect* share -- ``(N - k) / N * 100``. A healthy dataset
keeps direct mentions at or below ~10% (score >= 90). Matching is lexical
(tool name, its un-prefixed form, and the underscores-as-spaces form), so it
catches prompts that read like tool invocations rather than real-world goals.
"""

import logging

from scorers.dataset_quality.context import (
    DatasetQualityContext,
    SubScoreContribution,
)


class NamingDistributionScorer:
    """Fraction of CUJs that express intent instead of naming a tool."""

    category = "discoverability_coverage"

    def __init__(self, config: dict, global_models):
        self.name = "naming_distribution"
        config = config or {}
        self.weight = float(config.get("weight", 5))
        # Only shapes the suggestion, not the score (which is a plain proportion).
        self.target_named_fraction = float(
            config.get("target_named_fraction", 0.10)
        )

    @staticmethod
    def _surface_forms(tool_name: str) -> set[str]:
        bare = tool_name.split("__")[-1]
        return {
            tool_name.lower(),
            bare.lower(),
            bare.replace("_", " ").lower(),
        }

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False, logs="no scenarios")

        forms = set()
        for tool_name in context.tool_names:
            forms.update(self._surface_forms(tool_name))
        if not forms:
            return SubScoreContribution(
                applicable=False, logs="no tools in schema"
            )

        n_named = 0
        for scenario in context.scenarios:
            text = " ".join(
                str(scenario.get(field) or "")
                for field in ("starting_prompt", "conversation_plan")
            ).lower()
            if any(form in text for form in forms):
                n_named += 1

        score = round((n - n_named) / n * 100, 2)

        suggestions = []
        if n_named / n > self.target_named_fraction:
            suggestions.append(
                f"{n_named}/{n} CUJs name a tool directly; aim for "
                f"<={int(self.target_named_fraction * 100)}% so users are graded "
                "on discovering tools from intent."
            )
        logging.info(
            "naming_distribution: \t%d/%d name a tool -> %.2f",
            n_named, n, score,
        )
        return SubScoreContribution(
            score=score,
            row_fields={"dq_tool_named_count": n_named},
            suggestions=suggestions,
            logs=f"named={n_named}/{n}",
        )
