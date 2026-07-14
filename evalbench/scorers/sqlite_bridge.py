"""SQLite Ground Truth Resolution Adapter for EvalBench."""

import os
import sqlite3

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
