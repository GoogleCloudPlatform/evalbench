"""Bridge scorer that grades a CUJ dataset's intrinsic quality.

Adapts the holistic dataset-quality sub-scorers (each ``run(context) ->
SubScoreContribution``) to the standard :class:`~scorers.comparator.Comparator`
interface so they run inside the normal agent scoring path. The whole dataset
arrives as one wrapper scenario (``all_cujs``; see
``dataset.load_dataset_quality_json``), so ``compare`` is invoked once: it fetches
the product's tool schema, runs every configured sub-scorer, and rolls the
sub-scores up into a single weighted global score + letter grade.
"""

import json
import logging
from typing import Any, Tuple

from generators.models.mcp_tools import McpToolsGenerator
from scorers.comparator import Comparator
from scorers.dataset_quality.composition import CompositionScorer
from scorers.dataset_quality.context import DatasetQualityContext
from scorers.dataset_quality.error_recovery import ErrorRecoveryScorer
from scorers.dataset_quality.grading import ScoredMetric, compute_grade
from scorers.dataset_quality.naming_distribution import NamingDistributionScorer
from scorers.dataset_quality.parameter_coverage import ParameterCoverageScorer
from scorers.dataset_quality.trajectory_coverage import TrajectoryCoverageScorer
from scorers.dataset_quality.vague_examples import VagueExamplesScorer
from util.config import load_yaml_config


# Sub-scorers, keyed by the name used under the nested ``scorers:`` block.
SCORER_REGISTRY = {
    "trajectory_coverage": TrajectoryCoverageScorer,
    "naming_distribution": NamingDistributionScorer,
    "parameter_coverage": ParameterCoverageScorer,
    "vague_examples": VagueExamplesScorer,
    "error_recovery": ErrorRecoveryScorer,
    "composition": CompositionScorer,
}


class DatasetQualityScorer(Comparator):
    """Grades one CUJ dataset holistically; emits one weighted global score."""

    def __init__(self, config: dict, global_models):
        config = config or {}
        self.name = "dataset_quality"

        self.product_name = config.get("product_name")
        if not self.product_name:
            raise ValueError("dataset_quality scorer requires 'product_name'")

        self.model_config_path = config.get("model_config")
        if not self.model_config_path:
            raise ValueError(
                "dataset_quality scorer requires 'model_config' (the product's "
                "model config providing setup.mcp_servers for tool discovery)"
            )

        self.tools_generator = McpToolsGenerator(
            {"timeout": config.get("tools_timeout", 30)}
        )

        scorers_config = config.get("scorers") or {}
        if not scorers_config:
            raise ValueError("dataset_quality scorer requires a 'scorers' block")
        self.scorers = []
        for name, scorer_config in scorers_config.items():
            scorer_cls = SCORER_REGISTRY.get(name)
            if scorer_cls is None:
                raise ValueError(
                    f"dataset_quality: unknown scorer {name!r}; "
                    f"known: {', '.join(sorted(SCORER_REGISTRY))}"
                )
            self.scorers.append(scorer_cls(scorer_config or {}, global_models))

    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
        generated_error: Any,
        database: str = "",
        **kwargs,
    ) -> Tuple[float, str]:
        scenarios = self._extract_cujs(generated_eval_result)
        tools = self._fetch_tools()
        context = DatasetQualityContext(
            product_name=self.product_name,
            scenarios=scenarios,
            tools=tools,
        )

        sub_scores: dict[str, Any] = {}
        row_fields: dict[str, Any] = {}
        suggestions: list = []
        metrics = []
        for scorer in self.scorers:
            contribution = scorer.run(context)
            sub_scores[scorer.name] = contribution.score
            row_fields.update(contribution.row_fields)
            suggestions.extend(
                {
                    "scorer": scorer.name,
                    "category": scorer.category,
                    "text": text,
                }
                for text in contribution.suggestions
            )
            metrics.append(
                ScoredMetric(
                    name=scorer.name,
                    weight=scorer.weight,
                    category=scorer.category,
                    score=contribution.score,
                    applicable=contribution.applicable,
                )
            )

        grade = compute_grade(metrics)
        logs = json.dumps(
            {
                "product_name": self.product_name,
                "total_cujs": context.n,
                "dataset_quality_score": grade["dataset_quality_score"],
                "letter_grade": grade["letter_grade"],
                "category_scores": grade["category_scores"],
                "sub_scores": sub_scores,
                "row_fields": row_fields,
                "suggestions": suggestions,
            },
            default=str,
        )
        logging.info(
            "dataset_quality: %s -> %s (%s)",
            self.product_name,
            grade["dataset_quality_score"],
            grade["letter_grade"],
        )
        return grade["dataset_quality_score"] or 0, logs

    def _extract_cujs(self, eval_results: Any) -> list[dict]:
        """Pull the bundled CUJs out of the single wrapper scenario."""
        if isinstance(eval_results, str):
            try:
                eval_results = json.loads(eval_results) if eval_results else {}
            except json.JSONDecodeError:
                eval_results = {}
        scenario = (eval_results or {}).get("scenario", {}) or {}
        scenarios = scenario.get("all_cujs") or []
        if not scenarios:
            logging.warning("dataset_quality: no CUJs found in wrapper scenario")
        return scenarios

    def _fetch_tools(self) -> list:
        """Query the product model config's MCP servers for the tool catalog."""
        model_config = load_yaml_config(self.model_config_path)
        setup = model_config.get("setup") or {}
        if setup.get("extensions"):
            logging.warning(
                "dataset_quality: setup.extensions is not yet supported for "
                "tool discovery; only setup.mcp_servers is queried."
            )
        return self.tools_generator.fetch_tools_from_mcp_servers(
            setup.get("mcp_servers") or {}
        )
