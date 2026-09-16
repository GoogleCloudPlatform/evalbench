"""CUJ diversity scorer: how many distinct interaction paths the dataset exercises.

Runs the CUJ path classifier (each CUJ -> exactly one of the five paths: Happy,
Ambiguity & Clarification, Iterative Refinement, Error Recovery, Out-of-Domain).
Most datasets skew almost entirely to the Happy Path, which gives false confidence;
a healthy dataset also exercises the "unhappy" paths. The score is the fraction of
the five paths at least one CUJ exercises, and the full per-path counts are surfaced
as the dataset's path distribution.
"""

import logging

from scorers.dataset_quality.context import (
    CATEGORY_DIVERSITY,
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
from scorers.dataset_quality.prompts.cuj_path_classification import (
    CUJ_PATHS,
    CUJ_PATH_CLASSIFICATION_PROMPT,
    CUJ_PATH_CLASSIFICATION_SCHEMA,
    RECOMMENDATIONS_KEY,
)


class CujDiversityScorer(JudgeSubScorer):
    """Fraction of the five CUJ interaction paths the dataset exercises."""

    name = "cuj_diversity"
    category = CATEGORY_DIVERSITY
    default_weight = 15

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False)

        prompt = CUJ_PATH_CLASSIFICATION_PROMPT.format(
            tool_names=context.tool_names_str,
            cujs_json=context.cujs_json(DEFAULT_CUJ_FIELDS),
        )
        data = judge_labeled_json(
            self.model,
            prompt,
            CUJ_PATH_CLASSIFICATION_SCHEMA,
            CUJ_PATHS,
        )
        if data is None:
            return SubScoreContribution(applicable=False)
        path_ids = group_ids(data, CUJ_PATHS, context.cuj_ids)
        counts = {path: len(ids) for path, ids in path_ids.items()}

        covered = [path for path in CUJ_PATHS if path_ids[path]]
        missing = [path for path in CUJ_PATHS if not path_ids[path]]
        score = round(len(covered) / len(CUJ_PATHS) * 100)

        suggestions = []
        if missing:
            suggestions.append(
                "The dataset never exercises these interaction paths: "
                + ", ".join(missing)
                + ". Add CUJs on each so the agent is graded beyond the Happy Path."
            )

        logging.info(
            "cuj_diversity: \t%d/%d paths covered -> %d | %s",
            len(covered), len(CUJ_PATHS), score,
            ", ".join(f"{path}={counts[path]}" for path in CUJ_PATHS),
        )
        return SubScoreContribution(
            score=score,
            metrics={
                "paths_covered": len(covered),
                "paths_total": len(CUJ_PATHS),
            },
            suggestions=suggestions,
            example_prompts=(
                example_prompts(data, RECOMMENDATIONS_KEY) if missing else []
            ),
            evidence=path_ids,
            distribution={"cuj_path_distribution": counts},
        )
