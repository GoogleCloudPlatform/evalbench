"""Error & recovery coverage scorer: how much of the failure taxonomy a dataset tests.

Tags each CUJ with the failure/recovery modes it exercises (Invalid Request,
Permission Denied, Incomplete Result) and scores the fraction of that taxonomy the
dataset covers. A dataset can hit a healthy unhappy-path share yet still test only
one failure mode -- coverage catches that. (Interaction-path diversity is graded
separately by the cuj_diversity scorer.)
"""

import logging

from scorers.dataset_quality.context import (
    CATEGORY_ERROR_RECOVERY,
    DEFAULT_CUJ_FIELDS,
    DatasetQualityContext,
    JudgeSubScorer,
    SubScoreContribution,
)
from scorers.dataset_quality.llm import (
    example_prompts,
    group_ids,
    judge_labeled_json,
)
from scorers.dataset_quality.prompts.error_recovery_coverage import (
    ERROR_RECOVERY_COVERAGE_PROMPT,
    ERROR_RECOVERY_COVERAGE_SCHEMA,
    ERROR_RECOVERY_MODES,
    RECOMMENDATIONS_KEY,
)


class ErrorRecoveryScorer(JudgeSubScorer):
    """Fraction of the failure/recovery taxonomy the dataset exercises."""

    name = "error_recovery"
    category = CATEGORY_ERROR_RECOVERY
    default_weight = 20

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False)

        prompt = ERROR_RECOVERY_COVERAGE_PROMPT.format(
            tool_names=context.tool_names_str,
            tool_catalog=context.tool_catalog_json(),
            cujs_json=context.cujs_json(DEFAULT_CUJ_FIELDS),
        )
        data = judge_labeled_json(
            self.model,
            prompt,
            ERROR_RECOVERY_COVERAGE_SCHEMA,
            ERROR_RECOVERY_MODES,
        )
        if data is None:
            return SubScoreContribution(applicable=False)
        mode_ids = group_ids(data, ERROR_RECOVERY_MODES, context.cuj_ids)

        covered = [mode for mode in ERROR_RECOVERY_MODES if mode_ids[mode]]
        uncovered = [mode for mode in ERROR_RECOVERY_MODES if not mode_ids[mode]]
        score = round(len(covered) / len(ERROR_RECOVERY_MODES) * 100)

        suggestions = []
        if uncovered:
            suggestions.append(
                "The dataset never exercises these failure/recovery modes: "
                + ", ".join(uncovered)
                + ". Add CUJs where each occurs and the agent must recover "
                "(retry, correct params, surface the failure, or fall back)."
            )

        logging.info(
            "error_recovery: \t%d/%d modes covered -> %d",
            len(covered), len(ERROR_RECOVERY_MODES), score,
        )
        return SubScoreContribution(
            score=score,
            metrics={
                "dq_error_modes_covered": len(covered),
                "dq_error_modes_total": len(ERROR_RECOVERY_MODES),
            },
            suggestions=suggestions,
            example_prompts=(
                example_prompts(data, RECOMMENDATIONS_KEY) if uncovered else []
            ),
            evidence=mode_ids,
        )
