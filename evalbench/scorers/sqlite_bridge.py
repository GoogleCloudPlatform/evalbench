"""SQLite Ground Truth Resolution Adapter for EvalBench."""

import os
import sqlite3
import sys

import pandas as pd
import yaml


def get_sqlite_ground_truth(query: str) -> list:
    """Resolves candidate SQLite database files and executes query."""
    parent_dir = os.path.dirname(__file__)
    root_dir = os.path.abspath(os.path.join(parent_dir, "..", ".."))
    db_dir = os.path.join(root_dir, "db_connections", "bird")
    if not os.path.exists(db_dir):
        return []

    candidates = [
        f[:-7] for f in os.listdir(db_dir) if f.endswith(".sqlite")
    ]
    for cand in candidates:
        sqlite_path = os.path.join(db_dir, f"{cand}.sqlite")
        try:
            conn = sqlite3.connect(sqlite_path)
            df_cand = pd.read_sql_query(query, conn)
            conn.close()
            return df_cand.to_dict(orient="records")
        except Exception:
            continue

    return []


def is_hybrid_cross_db_enabled() -> bool:
    """Checks if hybrid_cross_db is supplied in experiment config."""
    for arg in sys.argv:
        if arg.startswith("--experiment_config="):
            config_path = arg.split("=", 1)[1]
            try:
                with open(config_path, "r") as f:
                    cfg = yaml.safe_load(f)
                py_scorer = cfg.get("scorers", {}).get("python_scorer", {})
                script = str(py_scorer.get("script_path", ""))
                name = str(py_scorer.get("scorer_name", ""))
                is_judge = "hybrid_xa_judge.py" in script
                is_name = "hybrid_cross_db" in name
                if is_judge or is_name:
                    return True
            except Exception:
                pass
    return False
