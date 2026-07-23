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
    CATEGORY_ERROR_RECOVERY,
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.grading import fraction_score
from scorers.dataset_quality.llm import tag_cujs
from scorers.dataset_quality.prompts.cuj_path_classification import (
    CUJ_PATHS,
    CUJ_PATH_CLASSIFICATION_PROMPT,
    CUJ_PATH_CLASSIFICATION_SCHEMA,
    PATH_AMBIGUITY,
    PATH_ERROR_RECOVERY,
    PATH_HAPPY,
    PATH_ITERATIVE_REFINEMENT,
    PATH_OUT_OF_DOMAIN,
)

# Path label -> BQ path-count column.
_PATH_COLUMNS = {
    PATH_HAPPY: "cuj_happy",
    PATH_AMBIGUITY: "cuj_ambiguity",
    PATH_ITERATIVE_REFINEMENT: "cuj_iterative_refinement",
    PATH_ERROR_RECOVERY: "cuj_error_recovery",
    PATH_OUT_OF_DOMAIN: "cuj_out_of_domain",
}


class ErrorRecoveryScorer:
    """Fraction of CUJs on the Error Recovery path, vs a target share."""

    category = CATEGORY_ERROR_RECOVERY

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

        path_ids: dict[str, list] = {path: [] for path in CUJ_PATHS}
        for s in context.scenarios:
            sid = s.get("id")
            path = tags.get(sid, {}).get("cuj_path")
            if path in path_ids:
                path_ids[path].append(sid)
        counts = {path: len(ids) for path, ids in path_ids.items()}

        n_error = counts[PATH_ERROR_RECOVERY]
        score = fraction_score(n_error, n, self.target_fraction)

        row_fields = {col: counts[path] for path, col in _PATH_COLUMNS.items()}
        row_fields["total_cujs"] = n

        suggestions = []
        if n_error / n < self.target_fraction:
            top_path = max(counts, key=counts.get)
            suggestions.append(
                f"Only {n_error}/{n} CUJs exercise the Error Recovery path (target ~"
                f"{int(self.target_fraction * 100)}%); the dataset skews to "
                f"{top_path} ({counts[top_path]}/{n}). Add CUJs where a tool call "
                "fails and the agent must recover (retry, correct params, fall back)."
            )
        paths_str = ", ".join(f"{path}={counts[path]}" for path in CUJ_PATHS)
        logging.info(
            "error_recovery: \t%d/%d error-recovery -> %.2f | paths: %s",
            n_error, n, score, paths_str,
        )
        return SubScoreContribution(
            score=score,
            row_fields=row_fields,
            suggestions=suggestions,
            evidence=path_ids,
            logs=f"error_recovery={n_error}/{n}, paths={counts}",
        )
