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
import time
from typing import Any, Tuple

from generators.models import get_generator
from generators.models.mcp_tools import McpToolsError, McpToolsGenerator
from scorers.comparator import Comparator
from scorers.dataset_quality.composition import CompositionScorer
from scorers.dataset_quality.context import DatasetQualityContext
from scorers.dataset_quality.cuj_diversity import CujDiversityScorer
from scorers.dataset_quality.error_recovery import ErrorRecoveryScorer
from scorers.dataset_quality.grading import ScoredMetric, compute_grade
from scorers.dataset_quality.naming_distribution import NamingDistributionScorer
from scorers.dataset_quality.parameter_coverage import ParameterCoverageScorer
from scorers.dataset_quality.synthesis import synthesize
from scorers.dataset_quality.trajectory_coverage import TrajectoryCoverageScorer
from scorers.dataset_quality.vague_examples import VagueExamplesScorer
from util.config import load_yaml_config


# Bounded retry for the live tool-discovery fetch, to ride out a transient
# network/ADC blip. A deterministic failure still surfaces after the last try.
_TOOL_FETCH_ATTEMPTS = 3
_TOOL_FETCH_BACKOFF_S = 1.0


# Sub-scorers, keyed by the name used under the nested ``sub_scorers:`` block.
SCORER_REGISTRY = {
    "trajectory_coverage": TrajectoryCoverageScorer,
    "naming_distribution": NamingDistributionScorer,
    "parameter_coverage": ParameterCoverageScorer,
    "vague_examples": VagueExamplesScorer,
    "error_recovery": ErrorRecoveryScorer,
    "composition": CompositionScorer,
    "cuj_diversity": CujDiversityScorer,
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

        scorers_config = config.get("sub_scorers") or {}
        if not scorers_config:
            raise ValueError(
                "dataset_quality scorer requires a 'sub_scorers' block"
            )
        self.scorers = []
        for name, scorer_config in scorers_config.items():
            scorer_cls = SCORER_REGISTRY.get(name)
            if scorer_cls is None:
                raise ValueError(
                    f"dataset_quality: unknown scorer {name!r}; "
                    f"known: {', '.join(sorted(SCORER_REGISTRY))}"
                )
            self.scorers.append(scorer_cls(scorer_config or {}, global_models))

        # Optional LLM synthesis pass. Absent -> deterministic report only. When
        # set, a single call turns the graded report (scores, counts, suggestions,
        # per-CUJ evidence) into an overall summary + prioritized actions, plus a
        # per-category assessment and recommendations merged into each category.
        synthesis_config = config.get("synthesis") or {}
        synthesis_model_config = synthesis_config.get("model_config")
        self.synthesis_model = (
            get_generator(global_models, synthesis_model_config)
            if synthesis_model_config
            else None
        )

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
    ) -> list[Tuple[str, float | None, str]]:
        scenarios = self._extract_cujs(generated_eval_result)
        try:
            tools = self._fetch_tools()
        except McpToolsError as e:
            # Tool discovery is infrastructure (network / ADC), not a property of
            # the dataset. Emit one ungraded row (null score) so a transient blip
            # is distinguishable from a genuine low grade rather than reading as
            # an F on the trends page. Unexpected errors are left to propagate.
            logging.error("dataset_quality: tool discovery failed: %s", e)
            return [(
                self.name,
                None,
                json.dumps(
                    {
                        "product_name": self.product_name,
                        "graded": False,
                        "error": f"tool discovery failed: {e}",
                    },
                    default=str,
                ),
            )]
        context = DatasetQualityContext(
            product_name=self.product_name,
            scenarios=scenarios,
            tools=tools,
        )

        # Group every scorer's output under the category it grades, so the report
        # is one card-per-category structure instead of parallel flat blocks.
        # Dataset-wide distributions (e.g. the CUJ-path breakdown) are hoisted to
        # the report's top level rather than nested under any one category.
        categories: dict[str, dict] = {}
        distributions: dict[str, Any] = {}
        metrics = []
        for scorer in self.scorers:
            contribution = scorer.run(context)
            metrics.append(
                ScoredMetric(
                    name=scorer.name,
                    weight=scorer.weight,
                    category=scorer.category,
                    score=contribution.score,
                    applicable=contribution.applicable,
                )
            )
            category = categories.setdefault(
                scorer.category,
                {
                    "name": scorer.category,
                    "score": None,
                    "sub_scores": {},
                    "metrics": {},
                    "gaps": [],
                    "evidence": {},
                },
            )
            category["sub_scores"][scorer.name] = contribution.score
            category["gaps"].extend(contribution.suggestions)
            category["evidence"].update(contribution.evidence)
            category["metrics"].update(contribution.row_fields)
            distributions.update(contribution.distribution)

        grade = compute_grade(metrics)
        for name, score in grade["category_scores"].items():
            if name in categories:
                categories[name]["score"] = score

        report = {
            "product_name": self.product_name,
            "total_cujs": context.n,
            "dataset_quality_score": grade["dataset_quality_score"],
            "letter_grade": grade["letter_grade"],
            "categories": list(categories.values()),
            **distributions,
        }
        if self.synthesis_model is not None:
            synthesize(self.synthesis_model, report)
        logging.info(
            "dataset_quality: %s -> %s (%s)",
            self.product_name,
            grade["dataset_quality_score"],
            grade["letter_grade"],
        )

        # Emit one row per category plus a top-level summary row, so each
        # sub-score lands on its own scores.csv row (mirroring how per-CUJ
        # scorers each get a row) rather than a single JSON blob. The summary
        # row carries the rollup that belongs to no single category.
        overall = grade["dataset_quality_score"]
        summary = {k: v for k, v in report.items() if k != "categories"}
        rows = [(self.name, overall if overall is not None else 0,
                 json.dumps(summary, default=str))]
        for category in report["categories"]:
            cat_score = category.get("score")
            rows.append((
                category["name"],
                cat_score if cat_score is not None else 0,
                json.dumps(category, default=str),
            ))
        return rows

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
        mcp_servers = setup.get("mcp_servers") or {}
        for attempt in range(1, _TOOL_FETCH_ATTEMPTS + 1):
            try:
                return self.tools_generator.fetch_tools_from_mcp_servers(
                    mcp_servers
                )
            except McpToolsError:
                if attempt == _TOOL_FETCH_ATTEMPTS:
                    raise
                logging.warning(
                    "dataset_quality: tool discovery attempt %d/%d failed; "
                    "retrying", attempt, _TOOL_FETCH_ATTEMPTS,
                )
                time.sleep(_TOOL_FETCH_BACKOFF_S * attempt)
        # Unreachable: the last attempt re-raises. Explicit terminal outcome so
        # the function never implicitly returns None against its -> list contract.
        raise McpToolsError("dataset_quality: tool discovery exhausted retries")
