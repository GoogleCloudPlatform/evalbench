"""Naming-distribution scorer: how many CUJs name a tool instead of intent.

Static (no judge). Counts CUJs whose user-facing text echoes a tool's name and
scores the *indirect* share -- ``(N - k) / N * 100``. Matching is lexical: the
tool name, its un-prefixed form, and the underscores-as-spaces form.
"""

import logging

from scorers.dataset_quality.context import (
    CATEGORY_DISCOVERABILITY,
    DatasetQualityContext,
    SubScoreContribution,
    SubScorer,
)

# Above this share of tool-naming CUJs, flag it in suggestions. Does not affect
# the score, which is a plain proportion.
_TARGET_NAMED_FRACTION = 0.10


class NamingDistributionScorer(SubScorer):
    """Fraction of CUJs that express intent instead of naming a tool."""

    name = "naming_distribution"
    category = CATEGORY_DISCOVERABILITY
    default_weight = 5

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
            return SubScoreContribution(applicable=False)

        forms = set()
        for tool_name in context.tool_names:
            forms.update(self._surface_forms(tool_name))
        if not forms:
            return SubScoreContribution(applicable=False)

        named_ids, intent_ids = [], []
        for scenario in context.scenarios:
            sid = scenario.get("id")
            text = " ".join(
                str(scenario.get(field) or "")
                for field in ("starting_prompt", "conversation_plan")
            ).lower()
            if any(form in text for form in forms):
                named_ids.append(sid)
            else:
                intent_ids.append(sid)
        n_named = len(named_ids)

        score = round((n - n_named) / n * 100, 2)

        suggestions = []
        if n_named / n > _TARGET_NAMED_FRACTION:
            suggestions.append(
                f"{n_named}/{n} CUJs name a tool directly; aim for "
                f"<={int(_TARGET_NAMED_FRACTION * 100)}% so users are graded "
                "on discovering tools from intent."
            )
        logging.info(
            "naming_distribution: \t%d/%d name a tool -> %.2f",
            n_named, n, score,
        )
        return SubScoreContribution(
            score=score,
            metrics={"dq_tool_named_count": n_named},
            suggestions=suggestions,
            evidence={"names_tool_ids": named_ids, "intent_based_ids": intent_ids},
        )
