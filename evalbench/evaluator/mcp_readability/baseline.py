"""Where the previous run's readability judgement is read back from.

A baseline is the most recent daily judgement for an endpoint, reconstructed
from the identity columns a run already writes. It exists so a caller can
compare this run's fingerprints against the previous one's.

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
from dataclasses import dataclass, field
import datetime
import glob
import json
import logging
import os


@dataclass
class Baseline:
    """The previous run's judgement for one endpoint, as loaded from a store."""

    endpoint_key: str
    job_id: str = ""
    check_timestamp: str = ""
    tool_fingerprints: dict = field(default_factory=dict)
    # The public feedback dict (findings_by_tool / waived / summary): the score
    # is stripped from that column, so it is carried separately.
    feedback: dict = field(default_factory=dict)
    readability_score: int = 0


# Result columns a baseline is reconstructed from.
_PRODUCT = "mcp_readability_product_name"
_SOURCE_URL = "mcp_readability_source_url"
_ENDPOINT_TYPE = "mcp_readability_endpoint_type"
_TOOL_FINGERPRINTS = "mcp_readability_tool_fingerprints_json"
_FEEDBACK = "mcp_readability_llm_feedback_json"
_SCORE = "mcp_readability_score"
_TIMESTAMP = "mcp_readability_check_timestamp"
_RUN_TAG = "mcp_readability_run_tag"
_JOB_ID = "job_id"

# Duplicated from the orchestrator rather than imported: the orchestrator reads
# this module, so importing back would be a cycle. A test pins them together.
_DAILY_RUN_TAG = "daily"

# The identity columns a key is built from, in order. Joined rather than
# hashed so the same expression can be written in SQL against columns the row
# already has, which is why no key column is persisted.
_KEY_COLUMNS = (_PRODUCT, _SOURCE_URL, _ENDPOINT_TYPE)
_KEY_SEPARATOR = "|"


def endpoint_key(product_name: str, source_url: str, endpoint_type: str) -> str:
    """Return the identity an endpoint's history hangs off.

    Renaming a product or moving its URL starts a new history. That is the
    honest outcome: the previous findings were about a different surface.
    """
    return _KEY_SEPARATOR.join(
        [product_name or "", source_url or "", endpoint_type or ""]
    )


def _row_key(row: dict) -> str:
    return endpoint_key(*[(row.get(c) or "").strip() for c in _KEY_COLUMNS])


# The same join expressed in SQL, so the two definitions of "same endpoint"
# cannot drift apart. Each column is trimmed and coalesced to match _row_key:
# SQL concatenation yields NULL if any operand is NULL, so a bare join would
# silently never match a row that _row_key matches by reading absent as "".
_KEY_SQL = f" || '{_KEY_SEPARATOR}' || ".join(
    f"COALESCE(TRIM({column}), '')" for column in _KEY_COLUMNS
)


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
        key = _row_key(row)
        if not key.strip(_KEY_SEPARATOR) or key not in wanted:
            return
        timestamp = _row_timestamp(row)
        existing = self._baselines.get(key)
        if existing is not None and existing.check_timestamp >= timestamp:
            return
        self._baselines[key] = Baseline(
            endpoint_key=key,
            job_id=(row.get(_JOB_ID) or "").strip(),
            check_timestamp=timestamp,
            tool_fingerprints=_load_json(row.get(_TOOL_FINGERPRINTS), {}),
            feedback=_load_json(row.get(_FEEDBACK), {}),
            readability_score=_safe_int(row.get(_SCORE)),
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
        self.dataset_id = dataset_id

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
                *_KEY_COLUMNS,
                _TIMESTAMP,
                _TOOL_FINGERPRINTS,
                _FEEDBACK,
                _SCORE,
                _JOB_ID,
            ]
        )
        # One row per endpoint, newest first. The lookback filters on the
        # readability timestamp, not the shared run_time column: readability
        # rows do not populate run_time, so a predicate on it would silently
        # match nothing. It is an ISO-8601 string, which orders
        # lexicographically by date.
        #
        # Only daily rows are eligible. An ad-hoc run is someone debugging,
        # often against an edited style guide or a subset of endpoints, and
        # letting one become the baseline would make the next scheduled run
        # compare against a surface nobody reviewed.
        query = f"""
            SELECT * EXCEPT(row_num) FROM (
              SELECT {columns}, ROW_NUMBER() OVER (
                  PARTITION BY {_KEY_SQL}
                  ORDER BY {_TIMESTAMP} DESC
              ) AS row_num
              FROM `{project}.{self.dataset_id}.results`
              WHERE {_KEY_SQL} IN UNNEST(@keys)
                AND {_TIMESTAMP} >= @cutoff
                AND {_RUN_TAG} = @run_tag
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
                bigquery.ScalarQueryParameter(
                    "run_tag", "STRING", _DAILY_RUN_TAG
                ),
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
    if store == "none":
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


def _row_timestamp(row: dict) -> str:
    return (row.get(_TIMESTAMP) or "").strip()


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
