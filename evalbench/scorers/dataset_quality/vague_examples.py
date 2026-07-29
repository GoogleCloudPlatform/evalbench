"""Discoverability scorer: how many CUJs pose a vague / indirect request.

The judge lists the vague / indirect CUJs; the score rewards datasets whose vague
share approaches ``target_fraction`` (default 0.5), capped at 100. Datasets of
only direct, tool-naming commands overstate how usable the product is.
"""

import logging

from scorers.dataset_quality.context import (
    CATEGORY_DISCOVERABILITY,
    DatasetQualityContext,
    JudgeSubScorer,
    SubScoreContribution,
)
from scorers.dataset_quality.grading import fraction_score
from scorers.dataset_quality.llm import group_cuj_ids
from scorers.dataset_quality.prompts.vague_examples import (
    KEY_VAGUE,
    VAGUE_EXAMPLES_PROMPT,
    VAGUE_EXAMPLES_SCHEMA,
)


class VagueExamplesScorer(JudgeSubScorer):
    """Fraction of CUJs whose request is vague / indirect, vs a target share."""

    name = "vague_examples"
    category = CATEGORY_DISCOVERABILITY
    default_weight = 12

    def __init__(self, config: dict, global_models):
        super().__init__(config, global_models)
        self.target_fraction = float((config or {}).get("target_fraction", 0.5))

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False)

        prompt = VAGUE_EXAMPLES_PROMPT.format(
            tool_names=context.tool_names_str,
            cujs_json=context.cujs_json(
                ["starting_prompt", "conversation_plan"]
            ),
        )
        grouped = group_cuj_ids(
            self.model,
            prompt,
            VAGUE_EXAMPLES_SCHEMA,
            (KEY_VAGUE,),
            context.cuj_ids,
        )
        if grouped is None:
            return SubScoreContribution(applicable=False)

        vague_ids = grouped[KEY_VAGUE]
        vague = set(vague_ids)
        direct_ids = [sid for sid in context.cuj_ids if sid not in vague]
        n_vague = len(vague_ids)
        score = fraction_score(n_vague, n, self.target_fraction)

        suggestions = []
        if n_vague / n < self.target_fraction:
            suggestions.append(
                f"Only {n_vague}/{n} CUJs are vague/indirect; aim for ~"
                f"{int(self.target_fraction * 100)}%. Add CUJs that state a goal "
                "without naming the tool, so the agent is tested on inferring intent."
            )
        logging.info("vague_examples: \t%d/%d vague -> %d", n_vague, n, score)
        return SubScoreContribution(
            score=score,
            metrics={"dq_vague_count": n_vague},
            suggestions=suggestions,
            evidence={KEY_VAGUE: vague_ids, "direct_ids": direct_ids},
        )
