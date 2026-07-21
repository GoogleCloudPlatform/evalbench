"""Parameter-coverage scorer: how much of the tool param surface CUJs exercise.

A judge decides, per ``(tool, parameter)``, which CUJs actually EXERCISE it --
i.e. which scenarios would force the agent to supply a value for that parameter,
inferred from the scenario text (CUJs carry no literal argument values). A
parameter is covered when at least ``min_items`` CUJs exercise it; the score is
``covered / total * 100``. The denominator is taken from the tool schema itself
(ground truth), not the judge, so a parameter the judge omits counts as uncovered.
A dataset that never varies a parameter can't tell you whether it's discoverable
or handled -- uncovered params are blind spots.
"""

import logging

from generators.models import get_generator
from scorers.dataset_quality.context import (
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.llm import judge_coverage
from scorers.prompt.parameter_coverage import PARAMETER_COVERAGE_PROMPT


class ParameterCoverageScorer:
    """Fraction of schema parameters exercised by at least ``min_items`` CUJs."""

    category = "discoverability_coverage"

    def __init__(self, config: dict, global_models):
        self.name = "parameter_coverage"
        config = config or {}
        self.weight = float(config.get("weight", 13))
        self.min_items = int(config.get("min_items", 1))
        model_config = config.get("model_config")
        if not model_config:
            raise ValueError(
                "model_config is required for the parameter_coverage scorer"
            )
        self.model = get_generator(global_models, model_config)

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False, logs="no scenarios")

        params = context.tool_parameters()
        if not params:
            return SubScoreContribution(
                applicable=False, logs="no parameters in schema"
            )

        prompt = PARAMETER_COVERAGE_PROMPT.format(
            tool_schema=context.tool_schema_json(),
            cujs_json=context.cujs_json(
                ["starting_prompt", "conversation_plan", "expected_trajectory"]
            ),
        )
        valid_ids = {s.get("id") for s in context.scenarios}

        counts: dict[tuple[str, str], set] = {}
        for entry in judge_coverage(self.model, prompt):
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
            suggestions.append(
                f"No CUJ (>= {self.min_items}) exercises these parameters: "
                + ", ".join(f"{tool}.{param}" for tool, param in uncovered)
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
