"""Orchestrator for intrinsic dataset-quality grading.

Grades one product's CUJ dataset on its own merits -- no agent run, no eval. Flow:
  1. Read the CUJ scenarios from ``dataset_config`` and fetch the product's tool
     schema from ``tools_source`` (same http/stdio/file fetcher as mcp_readability).
  2. Hand every scorer declared under ``scorers:`` the shared
     :class:`DatasetQualityContext`; each returns a 0-100 sub-score plus detail
     columns (see ``scorers.dataset_quality.context``).
  3. Roll the sub-scores into a weight-normalized global score, letter grade, and
     per-category rollups (``scorers.dataset_quality.grading``).

The orchestrator is scorer-agnostic: it instantiates whatever the run config
declares (via ``SCORER_REGISTRY``); adding a scorer needs only a registry entry
plus a run-config block.

Unlike the standard orchestrators, :meth:`process` returns ``None`` result/score
temp files so ``evalbench.py``'s shared reporters are skipped -- quality rows go
to a dedicated CSV (and optional dedicated BigQuery table) written here, not the
shared ``results`` table.
"""

import json
import logging
import os
import threading

import pandas as pd

from evaluator.orchestrator import Orchestrator
from generators.models.mcp_tools import McpToolsGenerator
from scorers.dataset_quality import (
    CompositionScorer,
    ErrorRecoveryScorer,
    NamingDistributionScorer,
    ParameterCoverageScorer,
    TrajectoryCoverageScorer,
    VagueExamplesScorer,
)
from scorers.dataset_quality.context import DatasetQualityContext
from scorers.dataset_quality.grading import ScoredMetric, compute_grade
from util.config import load_yaml_config


# Registered scorers, keyed by the name used under ``scorers:`` in the run config.
SCORER_REGISTRY = {
    "trajectory_coverage": TrajectoryCoverageScorer,
    "naming_distribution": NamingDistributionScorer,
    "parameter_coverage": ParameterCoverageScorer,
    "vague_examples": VagueExamplesScorer,
    "error_recovery": ErrorRecoveryScorer,
    "composition": CompositionScorer,
}


class DatasetQualityOrchestrator(Orchestrator):
    """Grades one CUJ dataset's intrinsic quality (no eval / agent run)."""

    def __init__(self, config, db_configs, setup_config, report_progress=False):
        super().__init__(config, db_configs, setup_config, report_progress)

        self.product_name = config.get("product_name")
        if not self.product_name:
            raise ValueError("dataset_quality requires 'product_name'")

        self.dataset_config = config.get("dataset_config")
        if not self.dataset_config:
            raise ValueError("dataset_quality requires 'dataset_config'")

        # The product's model config; its setup.mcp_servers are queried live for
        # the tool catalog.
        self.model_config_path = config.get("model_config")
        if not self.model_config_path:
            raise ValueError(
                "dataset_quality requires 'model_config' (the product's model "
                "config providing setup.mcp_servers)"
            )

        self.reporting_config = config.get("reporting") or {}

        # Shared model registry for get_generator (lock + client cache); each
        # scorer instantiates its own judge from its model_config against this.
        self.global_models = {
            "lock": threading.Lock(),
            "registered_models": {},
        }

        self.tools_generator = McpToolsGenerator(
            {"timeout": config.get("tools_timeout", 30)}
        )

        scorers_config = config.get("scorers") or {}
        if not scorers_config:
            raise ValueError("dataset_quality requires a 'scorers' block")
        self.scorers = []
        for name, scorer_config in scorers_config.items():
            scorer_cls = SCORER_REGISTRY.get(name)
            if scorer_cls is None:
                raise ValueError(
                    f"dataset_quality: unknown scorer {name!r}; "
                    f"known: {', '.join(sorted(SCORER_REGISTRY))}"
                )
            self.scorers.append(
                scorer_cls(scorer_config or {}, self.global_models)
            )

        self.row = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------
    def evaluate(self, dataset=None):
        """Grade the dataset. Ignores the framework-loaded ``dataset`` arg and
        reads the raw CUJ scenarios itself so the whole dataset is graded
        unfiltered.
        """
        scenarios = self._load_scenarios()
        tools = self._fetch_tools()
        context = DatasetQualityContext(
            product_name=self.product_name,
            scenarios=scenarios,
            tools=tools,
        )

        row = {
            "job_id": self.job_id,
            "run_time": self.run_time,
            "product_name": self.product_name,
            "dataset_config": self.dataset_config,
            "total_cujs": context.n,
        }
        metrics = []
        suggestions = []
        weights = {}
        for scorer in self.scorers:
            contribution = scorer.run(context)
            row[f"{scorer.name}_score"] = contribution.score
            row.update(contribution.row_fields)
            suggestions.extend(contribution.suggestions)
            weights[scorer.name] = scorer.weight
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
        row["global_score"] = grade["global_score"]
        row["letter_grade"] = grade["letter_grade"]
        for category, score in grade["category_scores"].items():
            row[f"category_{category}"] = score
        row["weights_json"] = json.dumps(weights)
        row["suggestions_json"] = json.dumps(suggestions)

        self.row = row
        if self.report_progress:
            logging.info(
                "dataset_quality: %s -> %s (%s)",
                self.product_name,
                grade["global_score"],
                grade["letter_grade"],
            )

    def process(self):
        """Write the quality row to CSV (+ optional dedicated BQ table).

        Returns ``None`` result/score temp files so ``evalbench.py`` skips the
        shared reporters -- quality rows never touch the shared results table.
        """
        if self.row is not None:
            self._write_csv(self.row)
            bq_config = self.reporting_config.get("bigquery")
            if bq_config:
                from reporting.dataset_quality_bq import store_dataset_quality

                store_dataset_quality(bq_config, [self.row])

        return (self.job_id, self.run_time, None, None, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _load_scenarios(self) -> list[dict]:
        with open(self.dataset_config, "r") as f:
            data = json.load(f)
        scenarios = data.get("scenarios") or []
        if not scenarios:
            logging.warning(
                "dataset_quality: no scenarios in %s", self.dataset_config
            )
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

    def _write_csv(self, row: dict) -> None:
        csv_config = self.reporting_config.get("csv") or {}
        output_dir = csv_config.get("output_directory", "results")
        directory = os.path.join(output_dir, self.job_id)
        os.makedirs(directory, exist_ok=True)
        file_path = os.path.join(directory, "dataset_quality.csv")
        pd.DataFrame([row]).to_csv(file_path, index=False)
        logging.info("dataset_quality: wrote %s", file_path)
