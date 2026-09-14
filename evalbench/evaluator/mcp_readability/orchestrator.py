"""Orchestrator for the MCP style-guide readability check.

Flow per endpoint:
  1. The tools generator fetches the endpoint's tools and renders the man-page
     markup.
  2. Applicable exceptions (waivers) are gathered.
  3. Every scorer declared under ``scorers:`` in the run config is invoked with
     the shared per-endpoint context; each contributes result-row columns and a
     binary summary score (see ``scorers.mcp_readability_scoring``).
  4. A result row is assembled from the base identity columns plus every
     scorer's contribution.

The orchestrator is scorer-agnostic: it instantiates whatever scorers the run
config declares (via ``SCORER_REGISTRY``) and merges their contributions. Adding
a new scorer (e.g. conformance testing) needs only a registry entry and a
run-config block -- no changes here.

Failure handling is fail-fast: if any endpoint cannot be fetched or scored, the
exception propagates and the whole job aborts with nothing persisted. There is no
per-endpoint status; the failure surfaces in the run log. Isolate flaky or
experimental endpoints with a separate run config rather than per-row state.

Like the standard EvalBench orchestrators, :meth:`process` dumps the result rows
to a temp JSON and returns it as ``results_tf``; ``evalbench.py`` then hands that
to the shared reporters (CSV / BigQuery). The orchestrator performs no direct
CSV or BigQuery writes itself.
"""

import concurrent.futures
import datetime
import hashlib
import json
import logging
import os
import tempfile
import threading

from evaluator.orchestrator import Orchestrator
from generators.models import get_generator
from scorers import mcp_carry_forward as cf
from scorers.mcp_fingerprint import tool_fingerprints, toolset_fingerprint
from scorers.mcp_readability_scoring import EndpointContext
from scorers.mcp_style_readability import McpStyleReadabilityScorer
from scorers.mcp_tool_metrics import McpToolMetricsScorer
from util.config import load_yaml_config

from evaluator.mcp_readability import baseline as baseline_mod
from evaluator.mcp_readability import exceptions as exceptions_mod


# Registered mcp_readability scorers, keyed by the name used under ``scorers:`` in
# the run config (also each scorer's ``comparator`` in the summary). Add a new
# scorer here plus a run-config block to extend the check -- the orchestrator
# itself stays unchanged.
SCORER_REGISTRY = {
    "mcp_tool_metrics": McpToolMetricsScorer,
    "mcp_style_readability": McpStyleReadabilityScorer,
}


# Allowed endpoint_type values (deployment channel / dashboard categorization).
# Validated at load time; an unknown value fails the run fast.
ALLOWED_ENDPOINT_TYPES = ("PROD", "AUTOPUSH", "STAGING", "DEV")


# Base identity columns present on every result row (``job_id`` is the shared
# framework column). Each configured scorer appends its own COLUMNS; the full,
# canonical schema for a run is ``self.columns``. Columns are prefixed
# ``mcp_readability_`` so rows coexist with other eval types in the shared
# ``<project>.evalbench.results`` table (``mcp_readability_score`` non-null is the
# readability-row discriminator). No per-endpoint status column: a fetch/scoring
# failure aborts the whole job (fail-fast), so every persisted row is successful.
BASE_COLUMNS = [
    "mcp_readability_product_name",
    "mcp_readability_source_url",
    "mcp_readability_endpoint_type",
    "mcp_readability_check_timestamp",
    # Unambiguous run ordering. The column above is naive local time, so rows
    # written from google3 and from OSS interleave wrongly when sorted; the
    # baseline store orders on this one and falls back to the naive column only
    # for rows written before it existed.
    "mcp_readability_check_timestamp_utc",
    # Carry-forward identity: which endpoint this row is about, and the exact
    # tool surface it was judged against.
    "mcp_readability_endpoint_key",
    "mcp_readability_toolset_fingerprint",
    "mcp_readability_tool_fingerprints_json",
    "job_id",
]


def _validate_endpoint_type(value) -> str:
    """Normalize + validate an endpoint_type; raise on unknown (fail-fast)."""
    if value is None:
        raise ValueError("mcp_readability: endpoint_type is required")
    name = str(value).strip().upper()
    if name not in ALLOWED_ENDPOINT_TYPES:
        raise ValueError(
            f"mcp_readability: unknown endpoint_type {value!r}; "
            f"allowed: {', '.join(ALLOWED_ENDPOINT_TYPES)}"
        )
    return name


class McpReadabilityOrchestrator(Orchestrator):
    """Checks a list of MCP endpoints against an MCP tool style guide."""

    def __init__(self, config, db_configs, setup_config, report_progress=False):
        super().__init__(config, db_configs, setup_config, report_progress)

        runner_config = config.get("runners", {}) or {}
        self.endpoint_runners = runner_config.get("endpoint_runners", 4)

        # Load endpoints (a flat list; no shared defaults block).
        endpoints_config = config.get("endpoints_config")
        if not endpoints_config:
            raise ValueError("mcp_readability requires 'endpoints_config'")
        parsed = load_yaml_config(endpoints_config) or {}
        self.endpoints = parsed.get("endpoints") or []
        if not self.endpoints:
            logging.warning(
                "mcp_readability: no endpoints found in %s", endpoints_config
            )

        # Exceptions (waivers).
        self.all_exceptions = exceptions_mod.load_exceptions(
            config.get("exceptions_config")
        )

        # Carry-forward baselines. An absent baseline block yields the null
        # store, so every endpoint is judged in full exactly as today and the
        # google3-mirrored run config keeps working until it opts in.
        baseline_config = config.get("baseline") or {}
        self.baseline_store = baseline_mod.build_store(baseline_config)
        self.baseline_max_age_days = int(
            baseline_config.get("max_age_days", 90)
        )
        self.force_refresh = _force_refresh(baseline_config)
        self.force_refresh_products = {
            str(p).strip().lower()
            for p in (baseline_config.get("force_refresh_products") or [])
        }
        # Set when priming the store fails, so every endpoint reports
        # baseline_unavailable rather than silently looking like a first run.
        self.baseline_unavailable = False

        # Optional endpoint_type filter (validated against the allowed set).
        type_filter = config.get("endpoint_types") or []
        self.endpoint_type_filter = {
            _validate_endpoint_type(t) for t in type_filter
        }

        # Shared model registry for get_generator (lock + cache): get_generator
        # memoizes instantiated model clients here (guarded by the lock) so
        # repeated calls -- e.g. across scorers and endpoint threads -- reuse the
        # same client instead of re-instantiating one per call.
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

        # Plug-and-play scorers: instantiate exactly what the run config declares.
        # Each scorer owns and validates its own config block (token_budget for
        # metrics; model_config + style_guide for the LLM judge).
        scorers_config = config.get("scorers") or {}
        if not scorers_config:
            raise ValueError("mcp_readability requires a 'scorers' block")
        self.scorers = []
        for name, scorer_config in scorers_config.items():
            scorer_cls = SCORER_REGISTRY.get(name)
            if scorer_cls is None:
                raise ValueError(
                    f"mcp_readability: unknown scorer {name!r}; "
                    f"known: {', '.join(sorted(SCORER_REGISTRY))}"
                )
            self.scorers.append(
                scorer_cls(scorer_config or {}, self.global_models)
            )

        # Full canonical result schema for this run: base identity columns plus
        # every configured scorer's columns.
        self.columns = list(BASE_COLUMNS)
        for scorer in self.scorers:
            self.columns.extend(scorer.COLUMNS)

        self.rows = []
        self.score_rows = []

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------
    def evaluate(self, dataset=None):
        """Run the readability check for every (filtered) endpoint."""
        endpoints = self._filtered_endpoints()
        if not endpoints:
            logging.warning(
                "mcp_readability: no endpoints to check after filtering."
            )
            self.rows = []
            self.score_rows = []
            return

        # Prefetch every baseline on one thread before the pool starts: the
        # per-endpoint alternative is N round trips racing each other.
        self._prime_baselines(endpoints)

        workers = max(1, int(self.endpoint_runners))
        results = []  # list[(row, score_rows)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._check_endpoint, ep): ep for ep in endpoints
            }
            # fail-fast: _check_endpoint lets exceptions propagate, so the first
            # failed future re-raises here and aborts the run.
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

        # Deterministic ordering (by product_name then source url).
        results.sort(
            key=lambda rs: (
                rs[0].get("mcp_readability_product_name", ""),
                rs[0].get("mcp_readability_source_url", ""),
            )
        )
        self.rows = [row for row, _ in results]
        self.score_rows = [s for _, score_rows in results for s in score_rows]
        if self.report_progress:
            logging.info(
                "mcp_readability: checked %d endpoints.", len(self.rows)
            )

    def process(self):
        """Dump result + score rows to temp JSONs and return them.

        Matches the standard orchestrator contract so ``evalbench.py`` + the
        shared reporters handle the CSV / BigQuery writes with no special casing:

        - ``results_tf`` holds the full per-endpoint readability rows (drives the
          evals output).
        - ``scores_tf`` holds one score row per (endpoint, scorer) so the shared
          analyzer aggregates a pass rate for each scorer key declared under
          ``scorers:`` (drives the scores / summary output).
        """
        results_tf = self._dump_temp_json(self.rows)
        scores_tf = self._dump_temp_json(self.score_rows)
        return (self.job_id, self.run_time, results_tf, scores_tf, None)

    # ------------------------------------------------------------------
    # Per-endpoint work
    # ------------------------------------------------------------------
    def _check_endpoint(self, endpoint: dict):
        """Fetch + score one endpoint. Returns ``(row, score_rows)``.

        Any failure propagates (fail-fast); the run aborts and nothing persists.
        """
        product_name = endpoint.get("product_name", "")
        endpoint_type = _validate_endpoint_type(endpoint.get("endpoint_type"))
        endpoint_url = self._endpoint_ref(endpoint)

        row = self._base_row(product_name, endpoint_url, endpoint_type)
        row["job_id"] = self.job_id
        endpoint_key = _endpoint_key(
            endpoint, product_name, endpoint_url, endpoint_type
        )
        row["mcp_readability_endpoint_key"] = endpoint_key

        try:
            # 1. Fetch tools + render man-page markup.
            tools, man_page = self.tools_generator.fetch_tools(endpoint)
            # 2. Fingerprint the tool surface. Written on every row regardless
            # of whether carry-forward is enabled, so turning it on later finds
            # usable baselines already in place.
            fingerprints = tool_fingerprints(tools)
            row["mcp_readability_toolset_fingerprint"] = toolset_fingerprint(
                fingerprints
            )
            row["mcp_readability_tool_fingerprints_json"] = json.dumps(
                fingerprints, sort_keys=True
            )

            # 3. Exceptions (waivers) for this endpoint.
            applicable = exceptions_mod.applicable_exceptions(
                endpoint, self.all_exceptions
            )

            # 4. Run every configured scorer against the shared context.
            context = EndpointContext(
                product_name=product_name,
                endpoint=endpoint,
                tools=tools,
                man_page=man_page,
                exceptions=applicable,
                baseline=self._baseline_context(
                    endpoint, endpoint_key, fingerprints
                ),
            )
            score_rows = []
            for scorer in self.scorers:
                contribution = scorer.run(context)
                row.update(contribution.row_fields)
                score_rows.append(
                    {
                        "id": product_name or endpoint_url,
                        "comparator": scorer.name,
                        "score": contribution.score,
                        "comparison_logs": contribution.logs,
                        "comparison_error": None,
                    }
                )
        except Exception:
            logging.exception(
                "mcp_readability: check failed for %s (%s); aborting run.",
                product_name,
                endpoint_url,
            )
            raise

        return row, score_rows

    # ------------------------------------------------------------------
    # Carry-forward baselines
    # ------------------------------------------------------------------
    def _prime_baselines(self, endpoints: list[dict]) -> None:
        """Prefetch the previous judgement for every endpoint in this run.

        A store failure is logged and downgraded to "no baselines", which costs
        a full re-judge and nothing else. That is a deliberate exception to this
        orchestrator's fail-fast ethos: a baseline is an optimisation, not a
        measurement, and the identity writing results may lack read permission.
        """
        keys = [
            _endpoint_key(
                ep,
                ep.get("product_name", ""),
                self._endpoint_ref(ep),
                _validate_endpoint_type(ep.get("endpoint_type")),
            )
            for ep in endpoints
        ]
        try:
            self.baseline_store.prime(keys)
        except Exception:
            self.baseline_unavailable = True
            logging.exception(
                "mcp_readability: could not load baselines; re-judging every "
                "endpoint in full."
            )

    def _baseline_context(
        self, endpoint: dict, endpoint_key: str, fingerprints: dict
    ):
        context = cf.BaselineContext(
            endpoint_key=endpoint_key,
            job_id=self.job_id,
            tool_fingerprints=fingerprints,
            toolset_fingerprint=toolset_fingerprint(fingerprints),
        )
        if self.baseline_unavailable:
            context.override_reason = cf.BASELINE_UNAVAILABLE
            return context
        try:
            found = self.baseline_store.load(endpoint_key)
        except Exception:
            logging.exception(
                "mcp_readability: could not load the baseline for %s; "
                "re-judging in full.",
                endpoint_key,
            )
            context.override_reason = cf.BASELINE_UNAVAILABLE
            return context
        # Attached even when it must not be reused: an expired or force-refreshed
        # baseline is still what "since the previous review" is measured against.
        # The override reason is what stops its findings being carried.
        context.baseline = found
        if self._forced(endpoint):
            context.override_reason = cf.FORCED_REFRESH
        elif found is not None and baseline_mod.is_expired(
            found, self.baseline_max_age_days
        ):
            context.override_reason = cf.BASELINE_EXPIRED
        return context

    def _forced(self, endpoint: dict) -> bool:
        """Whether this endpoint must be re-judged regardless of its baseline.

        Carry-forward otherwise entrenches the first run's draw: a hallucinated
        P0 could only be cleared by editing the tool. This is the escape hatch.
        """
        if self.force_refresh:
            return True
        product = str(endpoint.get("product_name", "")).strip().lower()
        return bool(product) and product in self.force_refresh_products

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _endpoint_ref(endpoint: dict) -> str:
        """A stable human-readable reference for an endpoint's tool source.

        Reported as ``mcp_readability_source_url``, and used as the sort key and
        the score-row id. Falls back to the file ``path`` for offline sources
        (which have no ``url``) so distinct endpoints don't collapse to "".
        """
        src = endpoint.get("tools_source") or {}
        return src.get("url") or src.get("path") or ""

    @staticmethod
    def _dump_temp_json(rows) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".json"
        ) as f:
            json.dump(rows, f, sort_keys=True, indent=4, default=str)
            return f.name

    def _filtered_endpoints(self) -> list[dict]:
        if not self.endpoint_type_filter:
            return list(self.endpoints)
        kept = []
        for ep in self.endpoints:
            if _validate_endpoint_type(ep.get("endpoint_type")) in (
                self.endpoint_type_filter
            ):
                kept.append(ep)
        return kept

    @staticmethod
    def _base_row(product_name, endpoint_url, endpoint_type) -> dict:
        return {
            "mcp_readability_product_name": product_name,
            "mcp_readability_source_url": endpoint_url,
            "mcp_readability_endpoint_type": endpoint_type,
            "mcp_readability_check_timestamp": (
                datetime.datetime.now().isoformat()
            ),
            "mcp_readability_check_timestamp_utc": (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            "job_id": "",
        }


def _endpoint_key(
    endpoint: dict,
    product_name: str,
    endpoint_url: str,
    endpoint_type: str,
) -> str:
    """Return the stable identity a baseline is looked up by.

    Derived from the same identity fields the row already reports, so no config
    change is needed to start using carry-forward. The cost is that renaming a
    product orphans its history. Set an explicit id: on the endpoint to survive
    renames; the rename then surfaces as endpoint_identity_changed rather than
    silently looking like a brand-new endpoint.
    """
    explicit = endpoint.get("id")
    if explicit:
        return str(explicit).strip()
    raw = "|".join(
        [product_name or "", endpoint_url or "", endpoint_type or ""]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _force_refresh(baseline_config: dict) -> bool:
    """Whether every endpoint must be re-judged this run.

    The environment variable exists for Guitar one-offs, where editing the
    mirrored run config is not practical.
    """
    if os.environ.get("EVALBENCH_MCP_FORCE_REFRESH", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return True
    return bool(baseline_config.get("force_refresh"))
