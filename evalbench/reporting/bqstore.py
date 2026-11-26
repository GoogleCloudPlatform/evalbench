from collections.abc import Iterator, Sequence
import json
import logging
import time
from typing import Any
import urllib.parse
import uuid

from google.api_core.exceptions import (
    Conflict,
    InternalServerError,
    NotFound,
    PreconditionFailed,
    ServiceUnavailable,
    TooManyRequests,
)
from google.cloud import bigquery
import pandas as pd
from pandas_gbq.schema.pandas_to_bigquery import dataframe_to_bigquery_fields
from reporting.report import Reporter, STORETYPE
from util.gcp import get_gcp_project

try:
    import google.colab  # type: ignore
    from IPython.display import display, HTML  # type: ignore

    _IN_COLAB = True
except ImportError:
    _IN_COLAB = False

_DEFAULT_CHUNK_SIZE = 500
# BigQuery tabledata.insertAll caps each HTTP request at 10 MB; flush below 8 MiB
# so large multi-turn conversation_history rows never hit 413/BadRequest.
_MAX_STREAMING_REQUEST_BYTES = 8 * 1024 * 1024
_MAX_SCHEMA_UPDATE_RETRIES = 5
_SUMMARY_AND_SCORES_INT_COLS = frozenset({
    "metric_score",
    "totalLatencyMs",
    "total_results_count",
    "correct_results_count",
})
_TRANSIENT_ROW_ERROR_REASONS = frozenset({
    "backendError",
    "internalError",
    "rateLimitExceeded",
    "timeout",
})

_REPORT_QUERY = """
WITH all_runs_with_set_tag AS (
    SELECT
        job_id,
        database,
        REPLACE(REPLACE(REPLACE(dialects, '[', ''),']',''),'\\\'','') AS dialect,
        id,
        nl_prompt,
        trim(generated_sql) AS generated_sql,
        golden_sql AS golden_sqls,
        eval_query AS eval_sqls,
        CASE
            WHEN generated_error IS NOT NULL THEN generated_error
            ELSE generated_result
        END AS generated_result,
        CASE
            WHEN golden_error IS NOT NULL THEN golden_error
            ELSE golden_result
        END AS golden_result,
        eval_results AS generated_eval_result,
        golden_eval_results AS golden_eval_result,
        DATE(run_time) AS date_of_eval
    FROM __DATASET__.results
    WHERE job_id = @eval_id
)
SELECT
    *,
    comparator = @correctness_scorer AS is_correctness_score,
    '__PROJECT_ID__' AS project_id
FROM all_runs_with_set_tag AS eval
LEFT JOIN (
    SELECT
        id,
        job_id,
        score,
        COALESCE(dialects[SAFE_OFFSET(0)],'') AS dialect,
        database,
        comparator,
        IFNULL(comparison_logs, '') AS comparison_logs
    FROM __DATASET__.scores
) AS scores USING (job_id, id, dialect, database)
ORDER BY date_of_eval DESC;
"""


def _split_dataframe(df: pd.DataFrame, chunk_size: int) -> Iterator[pd.DataFrame]:
    """Splits a pandas DataFrame into chunks of a specified size."""
    num_chunks = len(df) // chunk_size + (len(df) % chunk_size > 0)
    for i in range(num_chunks):
        start = i * chunk_size
        end = (i + 1) * chunk_size
        yield df[start:end]


def _infer_schema(
    df: pd.DataFrame, store_type: STORETYPE
) -> list[bigquery.SchemaField]:
    """Infers BigQuery SchemaFields via pandas-gbq with EvalBench overrides."""
    overrides: list[bigquery.SchemaField] = []
    if store_type in (STORETYPE.SUMMARY, STORETYPE.SCORES):
        overrides = [
            bigquery.SchemaField(str(col), "INTEGER")
            for col in df.columns
            if str(col) in _SUMMARY_AND_SCORES_INT_COLS
        ]

    fields = list(
        dataframe_to_bigquery_fields(
            df, override_bigquery_fields=overrides or None, index=False
        )
    )
    if store_type in (STORETYPE.SUMMARY, STORETYPE.SCORES):
        # Preserve legacy bqstore behavior: non-int float columns in SUMMARY /
        # SCORES are stored as STRING to avoid int/float schema lock conflicts.
        fields = [
            bigquery.SchemaField(f.name, "STRING")
            if f.mode != "REPEATED" and f.field_type.upper() in ("FLOAT", "FLOAT64")
            else f
            for f in fields
        ]
    return fields


def _coerce_json_value(val: Any, field: bigquery.SchemaField | None) -> Any:
    """Normalizes a pandas.to_json() value for BigQuery tabledata.insertAll."""
    if val is None:
        return None
    if field is not None and field.mode == "REPEATED":
        items = val if isinstance(val, list) else [val]
        elem_field = bigquery.SchemaField(
            field.name, field.field_type, mode="NULLABLE", fields=field.fields
        )
        return [
            v for e in items
            if (v := _coerce_json_value(e, elem_field)) is not None
        ]

    ftype = field.field_type.upper() if field is not None else "STRING"
    if ftype == "STRING":
        if isinstance(val, str):
            return val
        if isinstance(val, float):
            return str(int(val)) if val.is_integer() else str(val)
        return (
            json.dumps(val, allow_nan=False)
            if isinstance(val, (dict, list))
            else str(val)
        )

    # Empty strings in dtype="string" DataFrames represent NULL for non-STRING columns.
    if isinstance(val, str) and not val.strip():
        return None
    if ftype in ("INTEGER", "INT64") and isinstance(val, float):
        return int(round(val))
    if ftype == "DATETIME":
        try:
            ts = pd.Timestamp(val)
        except (TypeError, ValueError):
            return val
        if pd.isna(ts):
            return None
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        return ts.isoformat(timespec="microseconds")
    if ftype == "DATE" and isinstance(val, str) and "T" in val:
        return val.split("T", 1)[0]
    if ftype == "JSON" and not isinstance(val, str):
        return json.dumps(val, allow_nan=False)
    return val


def _dataframe_to_json_rows(
    chunk: pd.DataFrame, schema: Sequence[bigquery.SchemaField]
) -> list[dict[str, Any]]:
    """Converts a DataFrame chunk to BigQuery insertAll JSON dicts via pandas.to_json()."""
    schema_by_lower = {field.name.lower(): field for field in schema}
    # pandas.to_json() handles NumPy scalars, NaN/Inf/pd.NA/pd.NaT -> null
    # (including inside nested dicts/lists), exact 64-bit ints, and ISO dates.
    raw_rows = json.loads(
        chunk.to_json(
            orient="records",
            date_format="iso",
            date_unit="us",
            default_handler=str,
        )
    )
    rows: list[dict[str, Any]] = []
    for record in raw_rows:
        row: dict[str, Any] = {}
        for col, val in record.items():
            field = schema_by_lower.get(str(col).lower())
            target_col = field.name if field is not None else str(col)
            coerced = _coerce_json_value(val, field)
            if coerced is not None:
                row[target_col] = coerced
        rows.append(row)
    return rows


def _iter_streaming_batches(
    rows: Sequence[dict[str, Any]],
    max_rows: int,
    max_bytes: int | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Splits JSON rows by both count and serialized byte size for insertAll."""
    if max_bytes is None:
        max_bytes = _MAX_STREAMING_REQUEST_BYTES
    batch: list[dict[str, Any]] = []
    batch_bytes = 0
    for row in rows:
        row_bytes = len(json.dumps(row, allow_nan=False).encode("utf-8")) + 2
        if batch and (
            len(batch) >= max_rows or batch_bytes + row_bytes > max_bytes
        ):
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(row)
        batch_bytes += row_bytes
    if batch:
        yield batch


def _has_schema_lag_error(errors: Sequence[dict[str, Any]]) -> bool:
    """Returns True when any insert_rows_json row error indicates schema lag."""
    for row_err in errors:
        for err in row_err.get("errors") or []:
            message = str(err.get("message") or "").lower()
            if "no such field" in message:
                return True
    return False


def _is_transient_insert_error(errors: Sequence[dict[str, Any]]) -> bool:
    """Returns True when insert_rows_json row errors are transient or schema-lag."""
    if not errors:
        return False
    saw_retryable = False
    for row_err in errors:
        item_errors = row_err.get("errors") or []
        if not item_errors:
            return False
        for err in item_errors:
            reason = str(err.get("reason") or "")
            message = str(err.get("message") or "").lower()
            if reason == "stopped":
                # BigQuery aborts sibling rows with reason="stopped" whenever
                # skip_invalid_rows=False and another row in the batch fails.
                continue
            if reason in _TRANSIENT_ROW_ERROR_REASONS:
                saw_retryable = True
                continue
            # tabledata.insertAll caches schemas briefly after create_table or
            # update_table and can report "no such field" for a few seconds.
            if "no such field" in message:
                saw_retryable = True
                continue
            return False
    return saw_retryable


class BigQueryReporter(Reporter):
    def __init__(self, reporting_config, job_id, run_time):
        super().__init__(reporting_config, job_id, run_time)
        reporting_config = reporting_config or {}
        self.project_id = get_gcp_project(
            reporting_config.get("gcp_project_id"))
        self.location = reporting_config.get("dataset_location") or "US"
        self.client = bigquery.Client(project=self.project_id)
        self.dataset_id = reporting_config.get("dataset_id") or "evalbench"
        self.dataset_path = "{}.{}".format(self.project_id, self.dataset_id)
        self.configs_table = "{}.configs".format(self.dataset_path)
        self.results_table = "{}.results".format(self.dataset_path)
        self.scores_table = "{}.scores".format(self.dataset_path)
        self.summary_table = "{}.summary".format(self.dataset_path)

        # Make chunk size configurable, defaulting to 500
        self.chunk_size = int(reporting_config.get(
            "chunk_size") or _DEFAULT_CHUNK_SIZE)
        self._dataset_ensured = False
        self._tables: dict[str, bigquery.Table] = {}

    def _ensure_dataset(self) -> None:
        if self._dataset_ensured:
            return
        dataset = bigquery.Dataset(self.dataset_path)
        dataset.location = self.location
        self.client.create_dataset(dataset, exists_ok=True, timeout=30)
        self._dataset_ensured = True

    def _ensure_table_and_schema(
        self, table_id: str, df: pd.DataFrame, store_type: STORETYPE
    ) -> bigquery.Table:
        """Ensures `table_id` exists and includes any new columns in `df`."""
        table_obj = self._tables.get(table_id)
        if table_obj is None:
            try:
                table_obj = self.client.get_table(table_id)
            except NotFound:
                schema = _infer_schema(df, store_type)
                table_obj = self.client.create_table(
                    bigquery.Table(table_id, schema=schema), exists_ok=True
                )
                if not table_obj.schema:
                    table_obj = self.client.get_table(table_id)

        existing_lower = {field.name.lower() for field in table_obj.schema}
        missing_cols = [
            col for col in df.columns if str(col).lower() not in existing_lower
        ]
        if missing_cols:
            for attempt in range(_MAX_SCHEMA_UPDATE_RETRIES):
                # Refresh before patching in case another worker added the field.
                table_obj = self.client.get_table(table_id)
                existing_lower = {
                    field.name.lower() for field in table_obj.schema
                }
                missing_cols = [
                    col for col in df.columns
                    if str(col).lower() not in existing_lower
                ]
                if not missing_cols:
                    break
                added_fields = _infer_schema(df[missing_cols], store_type)
                table_obj.schema = list(table_obj.schema) + added_fields
                try:
                    table_obj = self.client.update_table(table_obj, ["schema"])
                    break
                except (Conflict, PreconditionFailed):
                    if attempt == _MAX_SCHEMA_UPDATE_RETRIES - 1:
                        raise
                    time.sleep(0.5 * (2 ** attempt))

        self._tables[table_id] = table_obj
        return table_obj

    def _resolve_table_id(self, store_type: STORETYPE) -> str:
        if store_type == STORETYPE.CONFIGS:
            return self.configs_table
        if store_type == STORETYPE.EVALS:
            return self.results_table
        if store_type == STORETYPE.SCORES:
            return self.scores_table
        if store_type == STORETYPE.SUMMARY:
            return self.summary_table
        raise ValueError(f"Unsupported store type: {store_type}")

    def _refresh_schema(
        self,
        table_id: str,
        results: pd.DataFrame,
        store_type: STORETYPE,
        current_schema: list[bigquery.SchemaField],
        chunk: pd.DataFrame,
        pending_positions: list[int],
        pending_rows: list[dict[str, Any]],
    ) -> tuple[list[bigquery.SchemaField], list[dict[str, Any]]]:
        """Refreshes cached table schema and re-coerces pending rows if it changed."""
        self._tables.pop(table_id, None)
        refreshed = self._ensure_table_and_schema(table_id, results, store_type)
        refreshed_schema = list(refreshed.schema)
        if refreshed_schema != current_schema:
            current_schema = refreshed_schema
            pending_rows = _dataframe_to_json_rows(
                chunk.iloc[pending_positions], current_schema
            )
        return current_schema, pending_rows

    def store(self, results, type: STORETYPE):
        if results is None or results.empty:
            logging.info(f"No results to store for {type}")
            return

        self._ensure_dataset()
        table = self._resolve_table_id(type)

        # Use BigQuery streaming inserts (tabledata.insertAll) instead of per-eval
        # load jobs so high-concurrency CI suites never exhaust BigQuery's hard
        # 1,500 load_job_per_table.long daily limit.
        max_retries = 5
        initial_delay = 2
        backoff_factor = 2

        table_obj = self._ensure_table_and_schema(table, results, type)
        current_schema = list(table_obj.schema)

        for chunk in _split_dataframe(results, self.chunk_size):
            chunk_schema = current_schema
            json_rows = _dataframe_to_json_rows(chunk, chunk_schema)
            chunk_offset = 0

            for batch in _iter_streaming_batches(json_rows, self.chunk_size):
                batch_len = len(batch)
                pending_positions = list(
                    range(chunk_offset, chunk_offset + batch_len)
                )
                chunk_offset += batch_len
                if current_schema != chunk_schema:
                    pending_rows = _dataframe_to_json_rows(
                        chunk.iloc[pending_positions], current_schema
                    )
                else:
                    pending_rows = batch
                pending_row_ids = [str(uuid.uuid4()) for _ in pending_rows]

                for attempt in range(max_retries):
                    try:
                        errors = self.client.insert_rows_json(
                            table, pending_rows, row_ids=pending_row_ids
                        )
                        if not errors:
                            break
                        if (
                            attempt < max_retries - 1
                            and _is_transient_insert_error(errors)
                        ):
                            failed_indices = sorted({
                                err["index"]
                                for err in errors
                                if isinstance(err.get("index"), int)
                                and 0 <= err["index"] < len(pending_rows)
                            })
                            if failed_indices:
                                pending_positions = [
                                    pending_positions[i]
                                    for i in failed_indices
                                ]
                                pending_rows = [
                                    pending_rows[i] for i in failed_indices
                                ]
                                pending_row_ids = [
                                    pending_row_ids[i] for i in failed_indices
                                ]
                            if _has_schema_lag_error(errors):
                                current_schema, pending_rows = (
                                    self._refresh_schema(
                                        table,
                                        results,
                                        type,
                                        current_schema,
                                        chunk,
                                        pending_positions,
                                        pending_rows,
                                    )
                                )
                            delay = initial_delay * (backoff_factor ** attempt)
                            logging.warning(
                                "BQ streaming write had transient/schema-lag "
                                f"errors: {errors[:2]}. Retrying in {delay}s... "
                                f"(Attempt {attempt + 1}/{max_retries})"
                            )
                            time.sleep(delay)
                            continue
                        raise RuntimeError(
                            f"BigQuery streaming insert into {table} failed: "
                            f"{errors[:3]}"
                        )
                    except (
                        TooManyRequests,
                        ServiceUnavailable,
                        InternalServerError,
                        NotFound,
                    ) as e:
                        if attempt == max_retries - 1:
                            logging.error(
                                f"BQ write failed after {max_retries} attempts: {e}"
                            )
                            raise
                        if isinstance(e, NotFound):
                            current_schema, pending_rows = self._refresh_schema(
                                table,
                                results,
                                type,
                                current_schema,
                                chunk,
                                pending_positions,
                                pending_rows,
                            )
                        delay = initial_delay * (backoff_factor ** attempt)
                        logging.warning(
                            f"BQ write failed with transient error: {e}. "
                            f"Retrying in {delay}s... "
                            f"(Attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(delay)

    def print_dashboard_links(self):
        report_date = self.run_time.strftime("%Y-%m-%d")
        report_name = f"{report_date} Evalbench Report (eval_id={self.job_id})"
        report_params = "{" + f'"eval_results.eval_id": "{self.job_id}"' + "}"
        report_query = _REPORT_QUERY.replace(
            "__PROJECT_ID__", self.project_id
        ).replace("__DATASET__", self.dataset_id)
        report_link = (
            "https://lookerstudio.google.com/reporting/create?"
            + "c.reportId=e7d7fc00-4268-45d6-b17b-160ca271a4d0"
            + "&ds.eval_results.connector=bigQuery"
            + "&ds.eval_results.type=CUSTOM_QUERY"
            + "&ds.eval_results.projectId="
            + urllib.parse.quote(self.project_id)
            + "&ds.eval_results.sql="
            + urllib.parse.quote(report_query)
            + "&ds.eval_results.billingProjectId="
            + urllib.parse.quote(self.project_id)
            + f"&r.reportName={urllib.parse.quote(report_name)}"
            + f"&params={urllib.parse.quote(report_params)}"
        )
        if _IN_COLAB:
            html_link = (
                "The evaluation report is now available on this "
                f"<a href=\"{report_link}\">Dashboard!</a>"
            )
            display(HTML(html_link))  # type: ignore
        else:
            print(
                "Results available at:\n"
                f"\033[1;34m{report_link}\033[0m\n---\n"
            )
