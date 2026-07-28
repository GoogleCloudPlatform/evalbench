"""Discoverability scorer: how many CUJs pose a vague / indirect request.

The judge lists the vague / indirect CUJs; the score rewards datasets whose vague
share approaches ``target_fraction`` (default 0.5), capped at 100. Datasets of
only direct, tool-naming commands overstate how usable the product is.
"""

import logging

from generators.models import get_generator
from scorers.dataset_quality.context import (
    CATEGORY_DISCOVERABILITY,
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.grading import fraction_score
from scorers.dataset_quality.llm import group_cuj_ids
from scorers.dataset_quality.prompts.vague_examples import (
    KEY_VAGUE,
    VAGUE_EXAMPLES_PROMPT,
    VAGUE_EXAMPLES_SCHEMA,
)


class VagueExamplesScorer:
    """Fraction of CUJs whose request is vague / indirect, vs a target share."""

    category = CATEGORY_DISCOVERABILITY

    def __init__(self, config: dict, global_models):
        self.name = "vague_examples"
        config = config or {}
        self.weight = float(config.get("weight", 12))
        self.target_fraction = float(config.get("target_fraction", 0.5))
        model_config = config.get("model_config")
        if not model_config:
            raise ValueError(
                "model_config is required for the vague_examples scorer"
            )
        self.model = get_generator(global_models, model_config)

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False, logs="no scenarios")

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
            return SubScoreContribution(applicable=False, logs="judge call failed")

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
        logging.info("vague_examples: \t%d/%d vague -> %.2f", n_vague, n, score)
        return SubScoreContribution(
            score=score,
            row_fields={"dq_vague_count": n_vague},
            suggestions=suggestions,
            evidence={"vague": vague_ids, "direct": direct_ids},
            logs=f"vague={n_vague}/{n}, target_fraction={self.target_fraction}",
        )
