"""SQLite Ground Truth Resolution Adapter for EvalBench."""

import os
import sqlite3

import pandas as pd

from databases.util import unique_column_names


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
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    df.columns = unique_column_names(list(df.columns))
    return df.to_dict(orient="records")
