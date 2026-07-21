"""Composition scorer: multi-tool interaction + sequencing sensitivity.

The judge tags each CUJ with two independent booleans -- ``is_multi_tool`` (does
success genuinely need more than one distinct tool/skill) and
``has_sequence_dependency`` (does success need a specific ordering). The two
sub-shares are averaged: a dataset of one-tool-per-scenario requests overstates
how well the product handles realistic, composite work.
"""

import logging

from generators.models import get_generator
from scorers.dataset_quality.context import (
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.llm import tag_cujs
from scorers.prompt.composition_coverage import COMPOSITION_COVERAGE_PROMPT


class CompositionScorer:
    """Average of the multi-tool share and the sequencing-dependency share."""

    category = "composition_coverage"

    def __init__(self, config: dict, global_models):
        self.name = "composition"
        config = config or {}
        self.weight = float(config.get("weight", 20))
        model_config = config.get("model_config")
        if not model_config:
            raise ValueError(
                "model_config is required for the composition scorer"
            )
        self.model = get_generator(global_models, model_config)

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False, logs="no scenarios")

        prompt = COMPOSITION_COVERAGE_PROMPT.format(
            tool_names=context.tool_names_str,
            cujs_json=context.cujs_json(
                ["starting_prompt", "conversation_plan", "expected_trajectory"]
            ),
        )
        tags = tag_cujs(self.model, prompt)

        n_multi = sum(
            1
            for s in context.scenarios
            if tags.get(s.get("id"), {}).get("is_multi_tool") is True
        )
        n_seq = sum(
            1
            for s in context.scenarios
            if tags.get(s.get("id"), {}).get("has_sequence_dependency") is True
        )

        multitool_score = round(n_multi / n * 100, 2)
        sequencing_score = round(n_seq / n * 100, 2)
        score = round((multitool_score + sequencing_score) / 2, 2)
        logging.info(
            "composition: \tmulti=%d/%d seq=%d/%d -> %.2f",
            n_multi, n, n_seq, n, score,
        )
        return SubScoreContribution(
            score=score,
            row_fields={
                "dq_multitool_count": n_multi,
                "dq_sequence_count": n_seq,
                "dq_multitool_score": multitool_score,
                "dq_sequencing_score": sequencing_score,
            },
            logs=(
                f"multitool={n_multi}/{n} ({multitool_score}), "
                f"sequencing={n_seq}/{n} ({sequencing_score})"
            ),
        )
