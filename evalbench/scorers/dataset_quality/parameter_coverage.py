"""Parameter-coverage scorer: how much of the tool param surface CUJs exercise.

Static (no judge). Enumerates every distinct named parameter across the tool
schema (each tool's ``inputSchema.properties``) and counts a parameter as covered
when at least ``min_items`` CUJs mention it. Score is ``covered / total * 100``.
A dataset that never varies a parameter can't tell you whether it's discoverable
or handled -- uncovered params are blind spots. Matching is lexical (the param
name and its underscores-as-spaces form) against the user-facing CUJ text, so it
is a rough proxy: generic names (``name``, ``id``) over-match and type-suffixed
names (``project_id`` vs "project ...") can under-match.
"""

import logging

from scorers.dataset_quality.context import (
    DatasetQualityContext,
    SubScoreContribution,
)


class ParameterCoverageScorer:
    """Fraction of schema parameters exercised by at least ``min_items`` CUJs."""

    category = "discoverability_coverage"

    def __init__(self, config: dict, global_models):
        self.name = "parameter_coverage"
        config = config or {}
        self.weight = float(config.get("weight", 5))
        self.min_items = int(config.get("min_items", 1))

    @staticmethod
    def _param_names(tool) -> set[str]:
        schema = getattr(tool, "inputSchema", None)
        if schema is None and isinstance(tool, dict):
            schema = tool.get("inputSchema")
        properties = (schema or {}).get("properties") or {}
        return set(properties)

    @staticmethod
    def _surface_forms(param: str) -> set[str]:
        return {param.lower(), param.replace("_", " ").lower()}

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False, logs="no scenarios")

        params = set()
        for tool in context.tools:
            params.update(self._param_names(tool))
        if not params:
            return SubScoreContribution(
                applicable=False, logs="no parameters in schema"
            )

        texts = [
            " ".join(
                str(scenario.get(field) or "")
                for field in ("starting_prompt", "conversation_plan")
            ).lower()
            for scenario in context.scenarios
        ]

        covered = set()
        for param in params:
            forms = self._surface_forms(param)
            hits = sum(
                1 for text in texts if any(form in text for form in forms)
            )
            if hits >= self.min_items:
                covered.add(param)

        uncovered = sorted(params - covered)
        score = round(len(covered) / len(params) * 100, 2)

        suggestions = []
        if uncovered:
            suggestions.append(
                f"No CUJ (>= {self.min_items}) exercises these parameters: "
                + ", ".join(uncovered)
            )
        logging.info(
            "parameter_coverage: \t%d/%d params covered -> %.2f",
            len(covered), len(params), score,
        )
        return SubScoreContribution(
            score=score,
            row_fields={
                "dq_param_covered": len(covered),
                "dq_param_total": len(params),
            },
            suggestions=suggestions,
            logs=f"covered={len(covered)}/{len(params)}",
        )
