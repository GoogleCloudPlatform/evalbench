"""Error & Recovery scorer: how many CUJs exercise the Error Recovery path.

Runs the CUJ path classifier once (each CUJ -> exactly one of the five paths).
The score rewards datasets whose Error Recovery share approaches
``target_fraction`` (default 0.20), capped at 100 -- most datasets only test the
Happy Path, giving false confidence. This scorer also emits the full per-path
counts, which the orchestrator stores as the dataset's path distribution.
"""

import logging

from generators.models import get_generator
from scorers.dataset_quality.context import (
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.llm import tag_cujs
from scorers.dataset_quality.prompts.cuj_path_classification import (
    CUJ_PATHS,
    CUJ_PATH_CLASSIFICATION_PROMPT,
    CUJ_PATH_CLASSIFICATION_SCHEMA,
)

# Path label -> BQ path-count column.
_PATH_COLUMNS = {
    "Happy": "cuj_happy",
    "Ambiguity & Clarification": "cuj_ambiguity",
    "Iterative Refinement": "cuj_iterative_refinement",
    "Error Recovery": "cuj_error_recovery",
    "Out-of-Domain": "cuj_out_of_domain",
}


class ErrorRecoveryScorer:
    """Fraction of CUJs on the Error Recovery path, vs a target share."""

    category = "error_recovery_coverage"

    def __init__(self, config: dict, global_models):
        self.name = "error_recovery"
        config = config or {}
        self.weight = float(config.get("weight", 28))
        self.target_fraction = float(config.get("target_fraction", 0.20))
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

        prompt = CUJ_PATH_CLASSIFICATION_PROMPT.format(
            tool_names=context.tool_names_str,
            cujs_json=context.cujs_json(
                ["starting_prompt", "conversation_plan", "expected_trajectory"]
            ),
        )
        tags = tag_cujs(self.model, prompt, CUJ_PATH_CLASSIFICATION_SCHEMA)

        counts = {path: 0 for path in CUJ_PATHS}
        for s in context.scenarios:
            path = tags.get(s.get("id"), {}).get("cuj_path")
            if path in counts:
                counts[path] += 1

        n_error = counts["Error Recovery"]
        denom = self.target_fraction * n
        score = min(100.0, n_error / denom * 100) if denom > 0 else 0.0
        score = round(score, 2)

        row_fields = {col: counts[path] for path, col in _PATH_COLUMNS.items()}
        row_fields["total_cujs"] = n
        paths_str = ", ".join(f"{path}={counts[path]}" for path in CUJ_PATHS)
        logging.info(
            "error_recovery: \t%d/%d error-recovery -> %.2f | paths: %s",
            n_error, n, score, paths_str,
        )
        return SubScoreContribution(
            score=score,
            row_fields=row_fields,
            logs=f"error_recovery={n_error}/{n}, paths={counts}",
        )
