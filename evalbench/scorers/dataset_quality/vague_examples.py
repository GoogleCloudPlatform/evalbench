"""Discoverability scorer: how many CUJs pose a vague / indirect request.

The judge tags each CUJ ``is_vague``; the score rewards datasets whose vague
share approaches ``target_fraction`` (default 0.5), capped at 100. Datasets of
only direct, tool-naming commands overstate how usable the product is.
"""

import logging

from generators.models import get_generator
from scorers.dataset_quality.context import (
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.llm import tag_cujs
from scorers.prompt.vague_examples import VAGUE_EXAMPLES_PROMPT


class VagueExamplesScorer:
    """Fraction of CUJs whose request is vague / indirect, vs a target share."""

    category = "discoverability_coverage"

    def __init__(self, config: dict, global_models):
        self.name = "vague_examples"
        config = config or {}
        self.weight = float(config.get("weight", 10))
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
        tags = tag_cujs(self.model, prompt)
        n_vague = sum(
            1
            for s in context.scenarios
            if tags.get(s.get("id"), {}).get("is_vague") is True
        )

        denom = self.target_fraction * n
        score = min(100.0, n_vague / denom * 100) if denom > 0 else 0.0
        score = round(score, 2)
        logging.info("vague_examples: \t%d/%d vague -> %.2f", n_vague, n, score)
        return SubScoreContribution(
            score=score,
            row_fields={"dq_vague_count": n_vague},
            logs=f"vague={n_vague}/{n}, target_fraction={self.target_fraction}",
        )
