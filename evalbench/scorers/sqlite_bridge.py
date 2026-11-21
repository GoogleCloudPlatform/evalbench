"""SQLite Ground Truth Resolution Adapter for EvalBench."""

import logging
import os
import sqlite3

import pandas as pd

from databases.util import unique_column_names
from util.safepath import contained_path


def get_sqlite_ground_truth(
    query: str,
    database: str,
    db_dir: str = "",
) -> list:
    """Resolves candidate SQLite database files and executes query."""

    try:
        sqlite_path = contained_path(db_dir, f"{database}.sqlite")
    except ValueError as e:
        logging.warning("Skipped the SQLite ground truth: %s", e)
        return []
    if not os.path.exists(sqlite_path):
        return []
    conn = sqlite3.connect(sqlite_path)
    try:
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    df.columns = unique_column_names(list(df.columns))
    return df.to_dict(orient="records")
