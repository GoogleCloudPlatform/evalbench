"""CSV writer for MCP style-guide compliance results.

Writes one row per checked endpoint with the fixed metric schema requested for
the Data Cloud MCP readability check, plus an ``environment`` column (after
``endpoint_type``) so results can be sliced by release channel.

Column order and types are stable:
  product_name (str), endpoint_url (str), endpoint_type (str/enum name),
  environment (str/enum name), check_timestamp (ISO-8601 str),
  check_status (str/enum name), p0_issues (int), p1_issues (int),
  p2_issues (int), total_tools (int), estimated_tokens (int),
  token_budget_used_percent (float), compliance_score (int),
  llm_feedback_json (str), llm_feedback_html (str), error_message (str)
"""

import logging
import os
import sys

import pandas as pd


COLUMNS = [
    "product_name",
    "endpoint_url",
    "endpoint_type",
    "environment",
    "check_timestamp",
    "check_status",
    "p0_issues",
    "p1_issues",
    "p2_issues",
    "total_tools",
    "estimated_tokens",
    "token_budget_used_percent",
    "compliance_score",
    "llm_feedback_json",
    "llm_feedback_html",
    "error_message",
]

_INT_COLUMNS = [
    "p0_issues",
    "p1_issues",
    "p2_issues",
    "total_tools",
    "estimated_tokens",
    "compliance_score",
]
_FLOAT_COLUMNS = ["token_budget_used_percent"]
_STRING_COLUMNS = [
    "product_name",
    "endpoint_url",
    "endpoint_type",
    "environment",
    "check_timestamp",
    "check_status",
    "llm_feedback_json",
    "llm_feedback_html",
    "error_message",
]


def _output_dir(output_directory: str) -> str:
    # Match reporting/csv.py: when run as the gRPC service, write to the shared
    # volume rather than the configured local directory.
    if sys.argv and sys.argv[0].endswith("eval_server.py"):
        return "/tmp_session_files/results"
    return output_directory or "results"


def write_compliance_csv(rows: list[dict], output_directory: str, job_id: str) -> str:
    """Write compliance ``rows`` to ``<output_dir>/<job_id>/mcp_readability_compliance.csv``.

    Returns the path written.
    """
    out_dir = _output_dir(output_directory)
    directory = os.path.join(out_dir, job_id)
    os.makedirs(directory, exist_ok=True)
    file_path = os.path.join(directory, "mcp_readability_compliance.csv")

    df = pd.DataFrame(rows, columns=COLUMNS)

    # Enforce types so the CSV schema is stable regardless of row contents.
    for col in _INT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")
    for col in _FLOAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype("float64")
    for col in _STRING_COLUMNS:
        df[col] = df[col].fillna("").astype("string")

    df.to_csv(file_path, index=False)
    logging.info(
        "Wrote MCP readability compliance CSV (%d rows) to %s",
        len(df),
        file_path,
    )
    return file_path
