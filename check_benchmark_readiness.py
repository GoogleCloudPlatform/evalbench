import os
import sys
import logging
from typing import List, Dict

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "evalbench"))

from evalbench.databases import get_database
from util.config import load_yaml_config

# Disable logs
logging.disable(logging.CRITICAL)
os.environ["GOOGLE_CLOUD_DISABLE_OPENTELEMETRY"] = "true"

def check_db(config_path: str, db_name: str, label: str):
    try:
        conf = load_yaml_config(config_path)
        conf["database_name"] = db_name
        db = get_database(conf, None)
        
        # Check connection and metadata
        meta = db.get_metadata()
        if not meta:
            print(f"  [FAIL] {label}: Connected but NO TABLES found in {db_name}")
            return False
            
        # Count tables
        num_tables = len(meta)
        
        # Count total rows (approx)
        total_rows = 0
        try:
            # Check first table for rows
            first_table = list(meta.keys())[0]
            # Handle quoting for GSQL vs others
            quote = '`' if "spanner_gsql" in config_path or "mysql" in config_path else '"'
            if "spanner_gsql" in config_path: quote = '`'
            if "postgres" in config_path or "spanner_pg" in config_path: quote = '"'
            
            # Simple count query
            # Note: DB.execute returns (result, eval_result, error)
            # result is list of dicts
            res, _, err = db.execute(f"SELECT COUNT(*) as c FROM {quote}{first_table}{quote}")
            if not err and res:
                total_rows = list(res[0].values())[0]
        except:
            pass

        status = "EMPTY" if total_rows == 0 else f"Has Data ({total_rows}+ rows)"
        print(f"  [OK]   {label:<25} : {num_tables} tables, {status}")
        return True

    except Exception as e:
        print(f"  [FAIL] {label}: Could not connect to {db_name}. Error: {e}")
        return False

def main():
    print("=== Benchmark Readiness Check ===")
    
    # Define Targets
    # Format: (ConfigPath, DBName, Label)
    targets = []
    
    engines = [
        ("datasets/db_configs/postgres.yaml", "PG"),
        ("datasets/db_configs/mysql.yaml", "MySQL"),
        ("datasets/db_configs/spanner_gsql.yaml", "SpanGSQL"),
        ("datasets/db_configs/spanner_pg.yaml", "SpanPG")
    ]

    # 1. Air Travel
    for conf, eng in engines:
        name = "air_travel"
        if "spanner_gsql" in conf: name = "air_travel_gsql"
        if "spanner_pg" in conf: name = "air_travel_pg"
        targets.append((conf, name, f"AirTravel ({eng})"))

    # 2. BAT
    for conf, eng in engines:
        name = "db_blog"
        if "spanner_gsql" in conf: name = "db_blog_gsql"
        if "spanner_pg" in conf: name = "db_blog_pg"
        targets.append((conf, name, f"BAT ({eng})"))

    # 3. BIRD (Sample: california_schools)
    for conf, eng in engines:
        name = "bird_california_schools"
        if "spanner_gsql" in conf: name += "_gsql"
        if "spanner_pg" in conf: name += "_pg"
        targets.append((conf, name, f"BIRD-CalSchools ({eng})"))

    # 4. BIAS (Sample: credit)
    for conf, eng in engines:
        name = "bias_credit"
        if "spanner_gsql" in conf: name += "_gsql"
        if "spanner_pg" in conf: name += "_pg"
        targets.append((conf, name, f"BIAS-Credit ({eng})"))

    # Run Checks
    all_pass = True
    for conf, name, label in targets:
        if not check_db(conf, name, label):
            all_pass = False
            
    print("\n===============================")
    if all_pass:
        print("READY: All sampled databases are healthy.")
    else:
        print("WARNING: Some databases failed checks.")

if __name__ == "__main__":
    main()
