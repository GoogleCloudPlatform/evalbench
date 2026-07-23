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
from scorers.dataset_quality.prompts.vague_examples import (
    VAGUE_EXAMPLES_PROMPT,
    VAGUE_EXAMPLES_SCHEMA,
)


class VagueExamplesScorer:
    """Fraction of CUJs whose request is vague / indirect, vs a target share."""

    category = "discoverability_coverage"

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
        tags = tag_cujs(self.model, prompt, VAGUE_EXAMPLES_SCHEMA)
        vague_ids, direct_ids = [], []
        for s in context.scenarios:
            sid = s.get("id")
            if tags.get(sid, {}).get("is_vague") is True:
                vague_ids.append(sid)
            else:
                direct_ids.append(sid)
        n_vague = len(vague_ids)

        denom = self.target_fraction * n
        score = min(100.0, n_vague / denom * 100) if denom > 0 else 0.0
        score = round(score, 2)

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
