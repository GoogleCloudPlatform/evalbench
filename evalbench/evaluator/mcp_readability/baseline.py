"""Where the previous run's readability judgement is read back from.

A baseline is the most recent persisted judgement for an endpoint. The scorer
compares this run's fingerprints against it and skips the model entirely when
nothing changed (see scorers.mcp_carry_forward).

There are three stores. NullBaselineStore is the default and never finds one,
so every run is a full judge, exactly as today. LocalResultsBaselineStore scans
results/*/evals.csv, bounded by max_runs because that directory grows without
limit. BigQueryBaselineStore queries the shared results table; it is the repo's
first BigQuery read path and reuses the project and dataset resolution that
reporting.bqstore uses for writes.

Two rules hold for all of them. Stores prime once and are then read from
memory, because _check_endpoint runs in a thread pool and a per-endpoint query
would be N round trips and racy against the store. And failure degrades rather
than aborts: a store error is logged and treated as "no baseline", costing a
re-judge and nothing else. That is a deliberate exception to the orchestrator's
fail-fast handling, since a baseline is an optimisation rather than a
measurement, and the identity writing results may have no read permission on
the dataset.
"""

from abc import ABC, abstractmethod
import csv
import datetime
import glob
import hashlib
import json
import logging
import os

from scorers.mcp_carry_forward import Baseline


# Result columns a baseline is reconstructed from.
_KEY = "mcp_readability_endpoint_key"
_TOOL_FINGERPRINTS = "mcp_readability_tool_fingerprints_json"
_JUDGE_FINGERPRINT = "mcp_readability_judge_fingerprint"
_JUDGE_COMPONENTS = "mcp_readability_judge_components_json"
_FEEDBACK = "mcp_readability_llm_feedback_json"
_SCORE = "mcp_readability_score"
_PROVENANCE = "mcp_readability_feedback_provenance_json"
_TIMESTAMP_UTC = "mcp_readability_check_timestamp_utc"
_TIMESTAMP_NAIVE = "mcp_readability_check_timestamp"
_JOB_ID = "job_id"

# Newest-first scan cap for the local store: results/ is never pruned, and a
# baseline older than this many runs is not worth the file I/O.
_DEFAULT_MAX_RUNS = 200

# How far back the BigQuery store looks. Anything older than the longest
# sensible max_age_days would be discarded by the expiry check anyway.
_LOOKBACK_DAYS = 400


class BaselineStore(ABC):
    """Prefetch baselines for a run, then serve them from memory."""

    @abstractmethod
    def prime(self, endpoint_keys: list[str]) -> None:
        """Fetch the baseline for every endpoint in this run, once."""
        pass

    @abstractmethod
    def load(self, endpoint_key: str) -> Baseline | None:
        """Return the primed baseline for one endpoint, or None if absent."""
        pass


class NullBaselineStore(BaselineStore):
    """No baselines: every endpoint is judged in full."""

    def prime(self, endpoint_keys: list[str]) -> None:
        return None

    def load(self, endpoint_key: str) -> Baseline | None:
        return None


class _RowBaselineStore(BaselineStore):
    """Shared reconstruction of baselines from result rows.

    Abstract; subclasses supply prime(), which decides where the rows come from.
    """

    def __init__(self):
        self._baselines: dict[str, Baseline] = {}

    def load(self, endpoint_key: str) -> Baseline | None:
        return self._baselines.get(endpoint_key)

    def _absorb(self, row: dict, wanted: set[str]) -> None:
        """Keep row as the baseline for its endpoint if it is the newest."""
        key = (row.get(_KEY) or "").strip()
        if not key or key not in wanted:
            return
        timestamp = _row_timestamp(row)
        existing = self._baselines.get(key)
        if existing is not None and existing.check_timestamp >= timestamp:
            return
        self._baselines[key] = Baseline(
            endpoint_key=key,
            job_id=(row.get(_JOB_ID) or "").strip(),
            check_timestamp=timestamp,
            judge_fingerprint=(row.get(_JUDGE_FINGERPRINT) or "").strip(),
            judge_components=_load_json(row.get(_JUDGE_COMPONENTS), {}),
            tool_fingerprints=_load_json(row.get(_TOOL_FINGERPRINTS), {}),
            feedback=_load_json(row.get(_FEEDBACK), {}),
            readability_score=_safe_int(row.get(_SCORE)),
            provenance=_load_json(row.get(_PROVENANCE), {}),
        )


class LocalResultsBaselineStore(_RowBaselineStore):
    """Baselines read from <output_directory>/*/evals.csv."""

    def __init__(self, results_dir: str = "results",
                 max_runs: int = _DEFAULT_MAX_RUNS):
        super().__init__()
        self.results_dir = results_dir
        self.max_runs = max(1, int(max_runs))

    def prime(self, endpoint_keys: list[str]) -> None:
        wanted = {k for k in endpoint_keys if k}
        if not wanted:
            return
        pattern = os.path.join(self.results_dir, "*", "evals.csv")
        # Newest mtime first, capped: the newest run holding a given endpoint
        # wins, and _absorb's timestamp comparison settles ties.
        paths = sorted(
            glob.glob(pattern), key=_safe_mtime, reverse=True
        )[: self.max_runs]
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        self._absorb(row, wanted)
            except (OSError, csv.Error) as e:
                logging.warning(
                    "mcp_readability: could not read baseline %s: %s", path, e
                )


class BigQueryBaselineStore(_RowBaselineStore):
    """Baselines read from the shared <project>.<dataset>.results table."""

    def __init__(self, gcp_project_id: str = "", dataset_id: str = "evalbench"):
        super().__init__()
        self.gcp_project_id = gcp_project_id
        self.dataset_id = dataset_id or "evalbench"

    def prime(self, endpoint_keys: list[str]) -> None:
        wanted = {k for k in endpoint_keys if k}
        if not wanted:
            return
        from google.cloud import bigquery
        from util.gcp import get_gcp_project

        project = get_gcp_project(self.gcp_project_id)
        client = bigquery.Client(project=project)
        columns = ", ".join(
            [
                _KEY,
                _TIMESTAMP_UTC,
                _TIMESTAMP_NAIVE,
                _JUDGE_FINGERPRINT,
                _JUDGE_COMPONENTS,
                _TOOL_FINGERPRINTS,
                _FEEDBACK,
                _SCORE,
                _PROVENANCE,
                _JOB_ID,
            ]
        )
        # One row per endpoint: the newest by UTC timestamp, falling back to the
        # older naive local-time column for rows written before it existed.
        #
        # The lookback filters on the readability timestamp, not on the shared
        # run_time column: readability rows do not populate run_time, so a
        # predicate on it would silently match nothing. Both timestamp columns
        # are ISO-8601 strings, which order lexicographically by date.
        query = f"""
            SELECT * EXCEPT(row_num) FROM (
              SELECT {columns}, ROW_NUMBER() OVER (
                  PARTITION BY {_KEY}
                  ORDER BY COALESCE({_TIMESTAMP_UTC}, {_TIMESTAMP_NAIVE}) DESC
              ) AS row_num
              FROM `{project}.{self.dataset_id}.results`
              WHERE {_KEY} IN UNNEST(@keys)
                AND COALESCE({_TIMESTAMP_UTC}, {_TIMESTAMP_NAIVE}) >= @cutoff
            ) WHERE row_num = 1
        """
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=_LOOKBACK_DAYS)
        ).isoformat()
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("keys", "STRING", sorted(wanted)),
                bigquery.ScalarQueryParameter("cutoff", "STRING", cutoff),
            ]
        )
        for row in client.query(query, job_config=job_config).result():
            self._absorb(dict(row), wanted)


def build_store(config: dict) -> BaselineStore:
    """Build the store named by the run config's baseline block.

    An absent or empty block yields a NullBaselineStore, so a config that has
    not opted in behaves exactly as it does today. An unknown store name raises,
    matching the orchestrator's fail-fast handling of unknown config values.
    """
    config = config or {}
    store = str(config.get("store") or "none").strip().lower()
    if store in ("", "none", "off", "disabled"):
        return NullBaselineStore()
    if store == "local":
        return LocalResultsBaselineStore(
            results_dir=config.get("results_dir") or "results",
            max_runs=int(config.get("max_runs") or _DEFAULT_MAX_RUNS),
        )
    if store == "bigquery":
        return BigQueryBaselineStore(
            gcp_project_id=config.get("gcp_project_id") or "",
            dataset_id=config.get("dataset_id") or "evalbench",
        )
    raise ValueError(
        f"mcp_readability: unknown baseline store {store!r}; "
        "allowed: none, local, bigquery"
    )


def is_expired(baseline: Baseline, max_age_days: int, now=None) -> bool:
    """Whether a baseline is too old to reuse.

    Expiry is staggered by the endpoint key so a fleet of endpoints primed on
    the same day does not all re-judge on the same later day. This does break
    the strict invariant, which is acceptable only because the resulting run
    reports baseline_expired, making it an announced re-judge rather than an
    unexplained count change.

    max_age_days of 0 disables expiry entirely. now defaults to wall-clock UTC
    and is injected by tests.
    """
    if max_age_days <= 0:
        return False
    stamp = _parse_timestamp(baseline.check_timestamp)
    if stamp is None:
        # An unparseable timestamp cannot prove freshness.
        return True
    now = now or datetime.datetime.now(datetime.timezone.utc)
    offset = _stagger(baseline.endpoint_key, max_age_days)
    return (now - stamp).days >= max_age_days + offset


def _stagger(endpoint_key: str, max_age_days: int) -> int:
    """Return a deterministic per-endpoint offset in [0, max_age_days).

    Hashed rather than read as hex, because an endpoint may carry an explicit
    id: from endpoints.yaml, which is arbitrary text.
    """
    if not endpoint_key:
        return 0
    digest = hashlib.sha256(endpoint_key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max_age_days


def _row_timestamp(row: dict) -> str:
    return (
        (row.get(_TIMESTAMP_UTC) or "").strip()
        or (row.get(_TIMESTAMP_NAIVE) or "").strip()
    )


def _parse_timestamp(value: str) -> datetime.datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        # Pre-existing rows recorded naive local time; assume UTC rather than
        # discard them, since the only use is a coarse age comparison.
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp


def _load_json(value, default):
    if value in (None, "", "null"):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _safe_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
