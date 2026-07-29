"""Bridge scorer that grades a CUJ dataset's intrinsic quality.

Adapts the holistic dataset-quality sub-scorers (each ``run(context) ->
SubScoreContribution``) to the standard :class:`~scorers.comparator.Comparator`
interface so they run inside the normal agent scoring path. The whole dataset
arrives as one wrapper scenario (``all_cujs``; see
``dataset.load_dataset_quality_json``), so ``compare`` is invoked once: it fetches
the product's tool schema, runs every configured sub-scorer, and rolls the
sub-scores up into a single weighted global score + letter grade.
"""

import concurrent.futures
import json
import logging
import time
from typing import Any, Tuple

from generators.models import get_generator
from generators.models.agent_cli import AgentCliGenerator
from generators.models.mcp_client import McpToolsError
from scorers.comparator import Comparator
from scorers.dataset_quality.composition import CompositionScorer
from scorers.dataset_quality.context import (
    DatasetQualityContext,
    SubScoreContribution,
)
from scorers.dataset_quality.cuj_diversity import CujDiversityScorer
from scorers.dataset_quality.error_recovery import ErrorRecoveryScorer
from scorers.dataset_quality.grading import ScoredMetric, compute_grade
from scorers.dataset_quality.naming_distribution import NamingDistributionScorer
from scorers.dataset_quality.parameter_coverage import ParameterCoverageScorer
from scorers.dataset_quality.render import render_report
from scorers.dataset_quality.synthesis import synthesize
from scorers.dataset_quality.trajectory_coverage import TrajectoryCoverageScorer
from scorers.dataset_quality.vague_examples import VagueExamplesScorer
from util.config import load_yaml_config


# Bounded retry for the live tool-discovery fetch, to ride out a transient
# network/ADC blip. A deterministic failure still surfaces after the last try.
_TOOL_FETCH_ATTEMPTS = 3
_TOOL_FETCH_BACKOFF_S = 1.0

# Judge scorers share one generator, and their calls bypass its rate limiter, so
# an unbounded fan-out puts every sub-scorer on the same quota at once and buys
# 429 backoff instead of speed.
_MAX_SCORER_WORKERS = 3


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

        self.tools_timeout = config.get("tools_timeout", 30)

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

        # Optional; absent -> deterministic report only. See ``synthesis``.
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
        if not scenarios:
            # Null score, not 0, so an empty/malformed all_cujs stays
            # distinguishable from a genuine F on the trends page.
            return [(
                self.name,
                None,
                json.dumps(
                    {
                        "product_name": self.product_name,
                        "graded": False,
                        "error": "no CUJs to grade (missing or empty all_cujs)",
                    },
                    default=str,
                ),
            )]
        try:
            tools = self._fetch_tools()
        except McpToolsError as e:
            # Tool discovery is infrastructure (network / ADC), not a property of
            # the dataset, so an ungraded null row keeps a transient blip
            # distinguishable from a genuine F.
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

        contributions = self._run_scorers(context)

        # Dataset-wide distributions (e.g. the CUJ-path breakdown) are hoisted to
        # the report's top level rather than nested under any one category.
        categories: dict[str, dict] = {}
        distributions: dict[str, Any] = {}
        metrics = []
        for scorer in self.scorers:
            contribution = contributions[scorer]
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
                    "example_prompts": [],
                    "evidence": {},
                },
            )
            category["sub_scores"][scorer.name] = contribution.score
            category["gaps"].extend(contribution.suggestions)
            category["example_prompts"].extend(contribution.example_prompts)
            category["evidence"].update(contribution.evidence)
            category["metrics"].update(contribution.metrics)
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
        logging.info("\n%s", render_report(report))

        # One row per category plus a top-level summary row, so each sub-score
        # lands on its own scores.csv row rather than in a single JSON blob. A
        # None score means nothing was gradeable, and stays null rather than
        # collapsing to a 0 that would read as a genuine F.
        summary = {k: v for k, v in report.items() if k != "categories"}
        rows = [(
            self.name,
            grade["dataset_quality_score"],
            json.dumps(summary, default=str),
        )]
        for category in report["categories"]:
            rows.append((
                category["name"],
                category.get("score"),
                json.dumps(category, default=str),
            ))
        return rows

    def _run_scorers(self, context: DatasetQualityContext) -> dict:
        """Run every sub-scorer, returning ``{scorer: SubScoreContribution}``.

        Sub-scorers are independent and LLM-bound, so a bounded pool pays roughly
        the slowest scorer's latency instead of the sum. Each completion is logged
        so a slow judge is visibly in flight rather than looking like a hang.
        """
        total = len(self.scorers)
        workers = min(_MAX_SCORER_WORKERS, total)
        logging.info(
            "dataset_quality: running %d sub-scorers (%d at a time)",
            total, workers,
        )
        contributions = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(s.run, context): s for s in self.scorers}
            for done, future in enumerate(
                concurrent.futures.as_completed(futures), start=1
            ):
                scorer = futures[future]
                try:
                    contributions[scorer] = future.result()
                except Exception:
                    # One broken scorer drops out of the weighted score rather
                    # than discarding every other scorer's completed work.
                    logging.exception(
                        "dataset_quality: %s raised; dropping it (%d/%d)",
                        scorer.name, done, total,
                    )
                    contributions[scorer] = SubScoreContribution(applicable=False)
                else:
                    logging.info(
                        "dataset_quality: %s finished (%d/%d)",
                        scorer.name, done, total,
                    )
        return contributions

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
                return AgentCliGenerator.fetch_mcp_tools(
                    mcp_servers, self.tools_timeout
                )
            except McpToolsError:
                if attempt == _TOOL_FETCH_ATTEMPTS:
                    raise
                logging.warning(
                    "dataset_quality: tool discovery attempt %d/%d failed; "
                    "retrying", attempt, _TOOL_FETCH_ATTEMPTS,
                )
                time.sleep(_TOOL_FETCH_BACKOFF_S * attempt)
        raise McpToolsError("dataset_quality: tool discovery exhausted retries")
