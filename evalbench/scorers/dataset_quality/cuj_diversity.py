"""CUJ diversity scorer: how many distinct interaction paths the dataset exercises.

Runs the CUJ path classifier (each CUJ -> exactly one of the five paths: Happy,
Ambiguity & Clarification, Iterative Refinement, Error Recovery, Out-of-Domain).
Most datasets skew almost entirely to the Happy Path, which gives false confidence;
a healthy dataset also exercises the "unhappy" paths. The score is the fraction of
the five paths at least one CUJ exercises, and the full per-path counts are surfaced
as the dataset's path distribution.
"""

import logging

from generators.models import get_generator
from scorers.dataset_quality.context import (
    CATEGORY_DIVERSITY,
    DEFAULT_CUJ_FIELDS,
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.llm import group_cuj_ids
from scorers.dataset_quality.prompts.cuj_path_classification import (
    CUJ_PATHS,
    CUJ_PATH_CLASSIFICATION_PROMPT,
    CUJ_PATH_CLASSIFICATION_SCHEMA,
)


class CujDiversityScorer:
    """Fraction of the five CUJ interaction paths the dataset exercises."""

    category = CATEGORY_DIVERSITY

    def __init__(self, config: dict, global_models):
        self.name = "cuj_diversity"
        config = config or {}
        self.weight = float(config.get("weight", 15))
        model_config = config.get("model_config")
        if not model_config:
            raise ValueError(
                "model_config is required for the cuj_diversity scorer"
            )
        self.model = get_generator(global_models, model_config)

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False, logs="no scenarios")

        prompt = CUJ_PATH_CLASSIFICATION_PROMPT.format(
            tool_names=context.tool_names_str,
            cujs_json=context.cujs_json(DEFAULT_CUJ_FIELDS),
        )
        path_ids = group_cuj_ids(
            self.model,
            prompt,
            CUJ_PATH_CLASSIFICATION_SCHEMA,
            CUJ_PATHS,
            context.cuj_ids,
        )
        if path_ids is None:
            return SubScoreContribution(applicable=False, logs="judge call failed")
        counts = {path: len(ids) for path, ids in path_ids.items()}

        covered = [path for path in CUJ_PATHS if path_ids[path]]
        missing = [path for path in CUJ_PATHS if not path_ids[path]]
        score = round(len(covered) / len(CUJ_PATHS) * 100, 2)

        suggestions = []
        if missing:
            suggestions.append(
                "The dataset never exercises these interaction paths: "
                + ", ".join(missing)
                + ". Add CUJs on each so the agent is graded beyond the Happy Path."
            )

        logging.info(
            "cuj_diversity: \t%d/%d paths covered -> %.2f | %s",
            len(covered), len(CUJ_PATHS), score,
            ", ".join(f"{path}={counts[path]}" for path in CUJ_PATHS),
        )
        return SubScoreContribution(
            score=score,
            row_fields={
                "dq_paths_covered": len(covered),
                "dq_paths_total": len(CUJ_PATHS),
            },
            suggestions=suggestions,
            evidence=path_ids,
            distribution={"cuj_path_distribution": counts},
            logs=f"paths_covered={len(covered)}/{len(CUJ_PATHS)}, counts={counts}",
        )
