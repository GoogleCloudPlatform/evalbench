"""Error & recovery coverage scorer: how much of the failure taxonomy a dataset tests.

Tags each CUJ with the failure/recovery modes it exercises (Access Denied,
Transient Failure, Malformed Output, Empty Result, Partial Result, Cascading
Failure) and scores the fraction of that taxonomy the dataset covers. A
dataset can hit a healthy unhappy-path share yet still test only one failure mode --
coverage catches that. (Interaction-path diversity is graded separately by the
cuj_diversity scorer.)
"""

import logging

from generators.models import get_generator
from scorers.dataset_quality.context import (
    CATEGORY_ERROR_RECOVERY,
    DEFAULT_CUJ_FIELDS,
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.llm import group_cuj_ids
from scorers.dataset_quality.prompts.error_recovery_coverage import (
    ERROR_RECOVERY_COVERAGE_PROMPT,
    ERROR_RECOVERY_COVERAGE_SCHEMA,
    ERROR_RECOVERY_MODES,
)


class ErrorRecoveryScorer:
    """Fraction of the failure/recovery taxonomy the dataset exercises."""

    category = CATEGORY_ERROR_RECOVERY

    def __init__(self, config: dict, global_models):
        self.name = "error_recovery"
        config = config or {}
        self.weight = float(config.get("weight", 28))
        model_config = config.get("model_config")
        if not model_config:
            raise ValueError(
                "model_config is required for the error_recovery scorer"
            )
        self.model = get_generator(global_models, model_config)

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False, logs="no scenarios")

        prompt = ERROR_RECOVERY_COVERAGE_PROMPT.format(
            tool_names=context.tool_names_str,
            cujs_json=context.cujs_json(DEFAULT_CUJ_FIELDS),
        )
        mode_ids = group_cuj_ids(
            self.model,
            prompt,
            ERROR_RECOVERY_COVERAGE_SCHEMA,
            ERROR_RECOVERY_MODES,
            context.cuj_ids,
        )
        if mode_ids is None:
            return SubScoreContribution(applicable=False, logs="judge call failed")

        covered = [mode for mode in ERROR_RECOVERY_MODES if mode_ids[mode]]
        uncovered = [mode for mode in ERROR_RECOVERY_MODES if not mode_ids[mode]]
        score = round(len(covered) / len(ERROR_RECOVERY_MODES) * 100, 2)

        suggestions = []
        if uncovered:
            suggestions.append(
                "The dataset never exercises these failure/recovery modes: "
                + ", ".join(uncovered)
                + ". Add CUJs where each occurs and the agent must recover "
                "(retry, correct params, surface the failure, or fall back)."
            )

        logging.info(
            "error_recovery: \t%d/%d modes covered -> %.2f",
            len(covered), len(ERROR_RECOVERY_MODES), score,
        )
        return SubScoreContribution(
            score=score,
            row_fields={
                "dq_error_modes_covered": len(covered),
                "dq_error_modes_total": len(ERROR_RECOVERY_MODES),
            },
            suggestions=suggestions,
            evidence=mode_ids,
            logs=f"modes={len(covered)}/{len(ERROR_RECOVERY_MODES)}",
        )
