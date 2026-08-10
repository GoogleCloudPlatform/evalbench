"""Composition scorer: multi-tool interaction + sequencing sensitivity.

The judge makes two independent judgments per CUJ -- multi-tool (does success
genuinely need more than one distinct tool/skill) and sequence dependency (does
success need a specific ordering) -- and the two shares are averaged. Not
applicable when the product exposes fewer than two composable units; a dataset
that simply never composes still scores 0.
"""

import logging

from scorers.dataset_quality.context import (
    CATEGORY_COMPOSITION,
    DEFAULT_CUJ_FIELDS,
    DatasetQualityContext,
    JudgeSubScorer,
    SubScoreContribution,
    expected_trajectory,
)
from scorers.dataset_quality.llm import group_cuj_ids
from scorers.dataset_quality.prompts.composition_coverage import (
    COMPOSITION_COVERAGE_PROMPT,
    COMPOSITION_COVERAGE_SCHEMA,
    COMPOSITION_KEYS,
    KEY_MULTI_TOOL,
    KEY_SEQUENCE_DEPENDENCY,
)

# Below this share (%), a composition dimension is flagged in suggestions only.
_LOW_SHARE = 50.0


class CompositionScorer(JudgeSubScorer):
    """Average of the multi-tool share and the sequencing-dependency share."""

    name = "composition"
    category = CATEGORY_COMPOSITION
    default_weight = 15

    def run(self, context: DatasetQualityContext) -> SubScoreContribution:
        n = context.n
        if n == 0:
            return SubScoreContribution(applicable=False)

        # Schema tools union trajectory entries, so skill-only products (whose
        # composable units are scripts, not MCP tools) still get scored.
        surface = set(context.tool_names)
        for scenario in context.scenarios:
            surface.update(expected_trajectory(scenario))
        if len(surface) < 2:
            return SubScoreContribution(applicable=False)

        prompt = COMPOSITION_COVERAGE_PROMPT.format(
            tool_names=context.tool_names_str,
            cujs_json=context.cujs_json(DEFAULT_CUJ_FIELDS),
        )
        grouped = group_cuj_ids(
            self.model,
            prompt,
            COMPOSITION_COVERAGE_SCHEMA,
            COMPOSITION_KEYS,
            context.cuj_ids,
        )
        if grouped is None:
            return SubScoreContribution(applicable=False)

        multi_ids = grouped[KEY_MULTI_TOOL]
        seq_ids = grouped[KEY_SEQUENCE_DEPENDENCY]
        n_multi, n_seq = len(multi_ids), len(seq_ids)

        multitool_score = round(n_multi / n * 100)
        sequencing_score = round(n_seq / n * 100)
        score = round((multitool_score + sequencing_score) / 2)

        suggestions = []
        weak = []
        if multitool_score < _LOW_SHARE:
            weak.append(f"multi-tool ({n_multi}/{n})")
        if sequencing_score < _LOW_SHARE:
            weak.append(f"order-dependent ({n_seq}/{n})")
        if weak:
            suggestions.append(
                "Few composite CUJs: " + " and ".join(weak) + ". Add CUJs whose "
                "success needs more than one distinct tool in a specific order, to "
                "test realistic multi-step work."
            )
        logging.info(
            "composition: \tmulti=%d/%d seq=%d/%d -> %d",
            n_multi, n, n_seq, n, score,
        )
        return SubScoreContribution(
            score=score,
            metrics={
                "dq_multitool_count": n_multi,
                "dq_sequence_count": n_seq,
                "dq_multitool_score": multitool_score,
                "dq_sequencing_score": sequencing_score,
            },
            suggestions=suggestions,
            evidence={KEY_MULTI_TOOL: multi_ids, KEY_SEQUENCE_DEPENDENCY: seq_ids},
        )
