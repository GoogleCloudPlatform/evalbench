"""Orchestrator for the MCP style-guide compliance (readability) check.

Flow per endpoint:
  1. The ``mcp_tools`` *generator* fetches/normalizes the endpoint's tools.yaml.
  2. Token usage is estimated against the endpoint's token budget.
  3. Applicable exceptions + any previous feedback are gathered.
  4. The ``mcp_style_compliance`` *scorer* (LLM) evaluates tools.yaml vs the
     style guide and returns issue counts / score / feedback.
  5. A result row is assembled with the full metric schema.

Results are written to a dedicated compliance CSV. This orchestrator does not
use the NL2SQL dataset/scores pipeline, so :meth:`process` returns ``None`` for
the results/scores temp files, which makes the standard EvalBench reporting path
skip cleanly.
"""

import concurrent.futures
import datetime
import json
import logging
import threading

import pandas as pd

from evaluator.orchestrator import Orchestrator
from generators.models import get_generator
from scorers.mcp_style_compliance import McpStyleComplianceScorer
from reporting.mcp_readability_csv import COLUMNS as mcp_csv_columns
from reporting.mcp_readability_csv import write_compliance_csv
from util.config import load_yaml_config

from evaluator.mcp_readability.enums import (
    CheckStatus,
    coerce_endpoint_type,
    coerce_environment,
)
from evaluator.mcp_readability import exceptions as exceptions_mod
from evaluator.mcp_readability.token_estimator import (
    token_budget_used_percent,
)


class McpReadabilityOrchestrator(Orchestrator):
    """Checks a list of MCP endpoints against the Data Cloud MCP style guide."""

    def __init__(self, config, db_configs, setup_config, report_progress=False):
        super().__init__(config, db_configs, setup_config, report_progress)

        runner_config = config.get("runners", {}) or {}
        self.endpoint_runners = runner_config.get("endpoint_runners", 4)

        # Load endpoints + shared defaults.
        endpoints_config = config.get("endpoints_config")
        if not endpoints_config:
            raise ValueError("mcp_readability requires 'endpoints_config'")
        parsed = load_yaml_config(endpoints_config) or {}
        self.defaults = parsed.get("defaults") or {}
        self.endpoints = parsed.get("endpoints") or []
        if not self.endpoints:
            logging.warning(
                "mcp_readability: no endpoints found in %s", endpoints_config
            )

        # Style guide text (fed verbatim into the scorer prompt).
        self.style_guide = self._read_text(config.get("style_guide"))

        # Exceptions (waivers).
        self.all_exceptions = exceptions_mod.load_exceptions(
            config.get("exceptions_config")
        )

        # Default token budget + environment filter.
        self.default_token_budget = config.get("token_budget", 0) or 0
        env_filter = config.get("environments") or []
        self.environment_filter = {str(e).strip().upper() for e in env_filter}

        # Previous feedback for run-to-run consistency.
        self.previous_feedback = self._load_previous_feedback(
            config.get("previous_results_csv")
        )

        # Shared model registry for get_generator (lock + cache), as used by
        # the standard orchestrators.
        self.global_models = {
            "lock": threading.Lock(),
            "registered_models": {},
        }

        # Tools generator (the "system under test" fetcher).
        tools_generator_config = config.get("tools_generator_config")
        if not tools_generator_config:
            raise ValueError("mcp_readability requires 'tools_generator_config'")
        self.tools_generator = get_generator(
            self.global_models, tools_generator_config
        )

        # Compliance scorer (LLM).
        scorer_config = (config.get("scorers") or {}).get(
            "mcp_style_compliance"
        ) or {}
        self.scorer = McpStyleComplianceScorer(scorer_config, self.global_models)

        self.rows = []

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------
    def evaluate(self, dataset=None):
        """Run the compliance check for every (filtered) endpoint."""
        endpoints = self._filtered_endpoints()
        if not endpoints:
            logging.warning("mcp_readability: no endpoints to check after filtering.")
            self.rows = []
            return

        workers = max(1, int(self.endpoint_runners))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._check_endpoint, ep): ep for ep in endpoints
            }
            rows = []
            for future in concurrent.futures.as_completed(futures):
                rows.append(future.result())

        # Keep deterministic ordering (by product_name then url).
        rows.sort(key=lambda r: (r.get("product_name", ""), r.get("endpoint_url", "")))
        self.rows = rows
        if self.report_progress:
            logging.info("mcp_readability: checked %d endpoints.", len(rows))

    def process(self):
        reporting = self.config.get("reporting") or {}
        output_directory = (reporting.get("csv") or {}).get(
            "output_directory", "results"
        )
        try:
            write_compliance_csv(self.rows, output_directory, self.job_id)
        except Exception:
            logging.exception("mcp_readability: failed to write compliance CSV")

        # Optionally append the same rows to the existing eval BigQuery table.
        if reporting.get("bigquery"):
            self._write_bigquery(reporting["bigquery"])

        # Return Nones so the standard NL2SQL reporting path is skipped.
        return (self.job_id, self.run_time, None, None, None)

    def _write_bigquery(self, bq_config):
        """Append compliance rows to the standard eval BigQuery table.

        Reuses the shared :class:`BigQueryReporter` so rows land in the existing
        ``<project>.evalbench.results`` table (schema auto-evolves to add the
        compliance columns); no dedicated dataset/table is created.
        """
        if not self.rows:
            return
        try:
            from reporting.bqstore import BigQueryReporter
            from reporting.report import STORETYPE

            df = pd.DataFrame(self.rows, columns=mcp_csv_columns)
            df["job_id"] = self.job_id
            reporter = BigQueryReporter(bq_config, self.job_id, self.run_time)
            reporter.store(df, STORETYPE.EVALS)
        except Exception:
            logging.exception("mcp_readability: failed to write BigQuery results")

    # ------------------------------------------------------------------
    # Per-endpoint work
    # ------------------------------------------------------------------
    def _check_endpoint(self, endpoint: dict) -> dict:
        product_name = endpoint.get("product_name", "")
        endpoint_url = endpoint.get("endpoint_url", "")
        endpoint_type = coerce_endpoint_type(self._ep_value(endpoint, "endpoint_type"))
        environment = coerce_environment(self._ep_value(endpoint, "environment"))
        token_budget = self._ep_value(
            endpoint, "token_budget", self.default_token_budget
        )

        row = self._base_row(
            product_name, endpoint_url, endpoint_type, environment
        )
        try:
            # 1. Fetch tools + render man-page markup. Failure here is a
            #    FETCH_ERROR.
            try:
                tools, man_page = self.tools_generator.fetch_tools(
                    endpoint, self.defaults
                )
            except Exception as e:
                logging.exception(
                    "mcp_readability: fetch failed for %s (%s)",
                    product_name,
                    endpoint_url,
                )
                row["check_status"] = CheckStatus.FETCH_ERROR.name
                row["error_message"] = f"{type(e).__name__}: {e}"
                return row

            # 2. Token estimate / tool count (recorded even if analysis fails).
            #    Metrics come from the man-page formatter; the percentage is
            #    computed against the configured per-endpoint budget.
            est_tokens = man_page.estimated_tokens
            row["total_tools"] = man_page.total_tools
            row["estimated_tokens"] = est_tokens
            row["token_budget_used_percent"] = token_budget_used_percent(
                est_tokens, token_budget
            )

            # 3. Exceptions + previous feedback for this endpoint.
            applicable = exceptions_mod.applicable_exceptions(
                endpoint, self.all_exceptions
            )
            prev = self.previous_feedback.get(endpoint_url)

            # 4. Compliance evaluation. Failure here is an ANALYSIS_ERROR.
            try:
                feedback = self.scorer.evaluate(
                    tools_markup=man_page.man_page,
                    style_guide=self.style_guide,
                    product_name=product_name,
                    exceptions=applicable,
                    previous_feedback=prev,
                )
            except Exception as e:
                logging.exception(
                    "mcp_readability: analysis failed for %s (%s)",
                    product_name,
                    endpoint_url,
                )
                row["check_status"] = CheckStatus.ANALYSIS_ERROR.name
                row["error_message"] = f"{type(e).__name__}: {e}"
                return row

            # 5. Success: the eval ran. Compliance findings are recorded as
            #    metrics independent of this status.
            row.update(
                {
                    "check_status": CheckStatus.SUCCESS.name,
                    "p0_issues": int(feedback.get("p0_issues", 0)),
                    "p1_issues": int(feedback.get("p1_issues", 0)),
                    "p2_issues": int(feedback.get("p2_issues", 0)),
                    "compliance_score": int(feedback.get("compliance_score", 0)),
                    "llm_feedback_json": json.dumps(feedback),
                    "llm_feedback_html": self.scorer.to_html(feedback),
                    "error_message": "",
                }
            )
        except Exception as e:
            logging.exception(
                "mcp_readability: internal error for %s (%s)",
                product_name,
                endpoint_url,
            )
            row["check_status"] = CheckStatus.INTERNAL_ERROR.name
            row["error_message"] = f"{type(e).__name__}: {e}"
        return row

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _ep_value(self, endpoint: dict, key: str, fallback=None):
        """Endpoint value, falling back to the shared ``defaults`` then a literal."""
        if key in endpoint:
            return endpoint.get(key)
        if key in self.defaults:
            return self.defaults.get(key)
        return fallback

    def _filtered_endpoints(self) -> list[dict]:
        if not self.environment_filter:
            return list(self.endpoints)
        kept = []
        for ep in self.endpoints:
            env = coerce_environment(self._ep_value(ep, "environment")).name
            if env in self.environment_filter:
                kept.append(ep)
        return kept

    @staticmethod
    def _base_row(product_name, endpoint_url, endpoint_type, environment) -> dict:
        return {
            "product_name": product_name,
            "endpoint_url": endpoint_url,
            "endpoint_type": endpoint_type.name,
            "environment": environment.name,
            "check_timestamp": datetime.datetime.now().isoformat(),
            "check_status": "",
            "p0_issues": 0,
            "p1_issues": 0,
            "p2_issues": 0,
            "total_tools": 0,
            "estimated_tokens": 0,
            "token_budget_used_percent": 0.0,
            "compliance_score": 0,
            "llm_feedback_json": "",
            "llm_feedback_html": "",
            "error_message": "",
        }

    @staticmethod
    def _read_text(path) -> str:
        if not path:
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            logging.warning("mcp_readability: could not read style guide %s: %s", path, e)
            return ""

    @staticmethod
    def _load_previous_feedback(csv_path) -> dict:
        """Map endpoint_url -> prior llm_feedback_json from a previous run CSV."""
        if not csv_path:
            return {}
        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            logging.warning(
                "mcp_readability: could not read previous_results_csv %s: %s",
                csv_path,
                e,
            )
            return {}
        if "endpoint_url" not in df.columns or "llm_feedback_json" not in df.columns:
            return {}
        mapping = {}
        for _, r in df.iterrows():
            url = r.get("endpoint_url")
            fb = r.get("llm_feedback_json")
            if isinstance(url, str) and isinstance(fb, str) and fb:
                mapping[url] = fb
        return mapping
