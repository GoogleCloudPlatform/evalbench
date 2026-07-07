"""Hybrid Execution Accuracy (XA) Cross-Database Evaluator for EvalBench."""

from decimal import Decimal
import json
import os
import sqlite3
import sys
from typing import List, Optional

import pandas as pd


def get_sqlite_ground_truth(
    query: str,
    database: str,
    db_dir: str = "",
) -> list:
    """Resolves candidate SQLite database files and executes query."""

    sqlite_path = os.path.join(db_dir, f"{database}.sqlite")
    if not os.path.exists(sqlite_path):
        return []
    conn = sqlite3.connect(sqlite_path)
    try:
        return pd.read_sql_query(query, conn).to_dict(orient="records")
    finally:
        conn.close()


def compare_result_sets(df_bq: pd.DataFrame, df_sqlite: pd.DataFrame) -> bool:
    """Compares two DataFrames ignoring column names and row order.

    Normalization rules:
    1. Floats are rounded to 4 decimal places for cross-engine consistency.
    2. Rows are sorted lexicographically by string representation.
    3. Trailing '.0' suffixes are stripped from stringified numeric values.
    """
    if df_bq is None or df_sqlite is None:
        return False

    if df_bq.empty and df_sqlite.empty:
        return True

    if df_bq.empty != df_sqlite.empty:
        return False

    def normalize_df(df: pd.DataFrame) -> list[tuple]:
        rows = []
        for _, r in df.iterrows():
            normalized_row = []
            for val in r:
                if pd.isna(val):
                    normalized_row.append(None)
                elif isinstance(val, (int, float, Decimal)):
                    try:
                        normalized_row.append(round(float(val), 4))
                    except (ValueError, TypeError):
                        normalized_row.append(str(val))
                else:
                    s = str(val).strip().lower()
                    if s.endswith(".0"):
                        s = s[:-2]
                    normalized_row.append(s)
            rows.append(tuple(normalized_row))
        rows.sort(key=lambda x: str(x))
        return rows

    try:
        bq_rows = normalize_df(df_bq)
        sqlite_rows = normalize_df(df_sqlite)
    except Exception:
        return False

    if len(bq_rows) != len(sqlite_rows):
        return False

    for r_bq, r_sqlite in zip(bq_rows, sqlite_rows):
        if len(r_bq) != len(r_sqlite):
            return False
        for val_bq, val_sqlite in zip(r_bq, r_sqlite):
            if val_bq != val_sqlite:
                return False

    return True


def main():
    try:
        input_data = json.load(sys.stdin)
        database = input_data.get("database", "")
        pred_rows = input_data.get("generated_execution_result")
        ref_sql = input_data.get("golden_query", "")

        sqlite_db_dir = input_data.get("sqlite_db_dir", "")
        sqlite_records = get_sqlite_ground_truth(
            ref_sql, database, sqlite_db_dir
        )
        df_sqlite = pd.DataFrame(sqlite_records)
        sqlite_res_str = json.dumps(sqlite_records)

        gen_err = input_data.get("generated_error")
        if pred_rows is None or isinstance(pred_rows, str) or gen_err:
            err_msg = gen_err or "Invalid prediction object"
            reason = (
                f"FAIL | BigQuery Error: {err_msg} | "
                f"SQLite Ground Truth Result: {sqlite_res_str}"
            )
            print(json.dumps({"score": 0.0, "reason": reason}))
            return

        if isinstance(pred_rows, list):
            df_bq = pd.DataFrame(pred_rows)
        else:
            df_bq = pd.DataFrame()

        match = compare_result_sets(df_bq, df_sqlite)
        score = 100.0 if match else 0.0
        if match:
            reason = f"PASS | SQLite Ground Truth Result: {sqlite_res_str}"
        else:
            bq_res_str = json.dumps(df_bq.to_dict(orient="records"))
            reason = (
                f"FAIL | BQ Prediction: {bq_res_str} vs "
                f"SQLite Ground Truth: {sqlite_res_str}"
            )
        print(json.dumps({"score": score, "reason": reason}))

    except Exception as e:
        err_reason = f"FAIL: Exception in hybrid judge: {e}"
        print(json.dumps({"score": 0.0, "reason": err_reason}))


if __name__ == "__main__":
    main()
