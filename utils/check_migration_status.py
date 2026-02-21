import os
import sys
import argparse
import logging
import sqlite3
from typing import Dict, Any

# Ensure evalbench is in path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "evalbench"))

from evalbench.databases import get_database
from utils.bird_migration.schema_extractor import extract_schema
from utils.bird_migration.ddl_generator import DDLGenerator
from util.config import load_yaml_config

def check_status():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite_path", required=True)
    parser.add_argument("--db_config", required=True)
    parser.add_argument("--target_db_name", required=True)
    args = parser.parse_args()

    # Suppress noise
    logging.disable(logging.CRITICAL)
    os.environ["GOOGLE_CLOUD_DISABLE_OPENTELEMETRY"] = "true"

    # 1. Extract Source Schema
    source_schema = extract_schema(args.sqlite_path)
    
    # 2. Connect to Target
    db_config = load_yaml_config(args.db_config)
    db_config["database_name"] = args.target_db_name
    
    dialect_name = "spanner_gsql" if "spanner_gsql" in args.db_config else \
                   "spanner_pg" if "spanner_pg" in args.db_config else \
                   "postgres" if "postgres" in args.db_config else \
                   "mysql" if "mysql" in args.db_config else "sqlite"
    
    generator = DDLGenerator(dialect_name)
    
    try:
        db = get_database(db_config, None)
        target_meta = db.get_metadata()
    except Exception as e:
        err_str = str(e).lower()
        if "does not exist" in err_str or "unknown database" in err_str:
            print(f"EMPTY: Database {args.target_db_name} does not exist yet.", file=sys.stderr)
            sys.exit(2) 
        print(f"FAILED: Connection error for {args.target_db_name}: {e}", file=sys.stderr)
        sys.exit(1)

    if not target_meta:
        print(f"EMPTY: No tables found in target DB {args.target_db_name}.", file=sys.stderr)
        sys.exit(2) 

    # 3. Verify Tables and Columns
    for table_name, source_table in source_schema.items():
        if table_name not in target_meta:
            print(f"FAILED: Table {table_name} missing in target DB {args.target_db_name}.", file=sys.stderr)
            sys.exit(1)
        
        target_cols = {c["name"]: c["type"].upper() for c in target_meta[table_name]}
        
        for source_col in source_table.columns:
            if source_col.name not in target_cols:
                print(f"FAILED: Column {source_col.name} missing in table {table_name} of target DB {args.target_db_name}.", file=sys.stderr)
                sys.exit(1)
            
            # Type Check (Fuzzy)
            is_pk = (source_col.pk > 0) or (source_col.name in source_table.primary_keys)
            expected_type = generator.map_type(source_col.type, is_pk).upper()
            actual_type = target_cols[source_col.name]
            
            # Normalization Groups
            text_types = ["TEXT", "CHARACTER VARYING", "VARCHAR", "STRING", "STRING(MAX)", "CLOB"]
            float_types = ["REAL", "DOUBLE", "DOUBLE PRECISION", "FLOAT", "FLOAT64"]
            int_types = ["INT", "INTEGER", "INT64", "BIGINT"]
            
            is_text_expected = any(t in expected_type for t in text_types)
            is_text_actual = any(t in actual_type for t in text_types)
            if is_text_expected and is_text_actual: continue
            
            is_float_expected = any(t in expected_type for t in float_types)
            is_float_actual = any(t in actual_type for t in float_types)
            if is_float_expected and is_float_actual: continue
            
            is_int_expected = any(t in expected_type for t in int_types)
            is_int_actual = any(t in actual_type for t in int_types)
            if is_int_expected and is_int_actual: continue

            if expected_type not in actual_type and actual_type not in expected_type:
                print(f"FAILED: Type mismatch for {table_name}.{source_col.name} in {args.target_db_name}: expected {expected_type}, got {actual_type}", file=sys.stderr)
                sys.exit(1)

    # 4. Verify Row Counts
    src_conn = sqlite3.connect(args.sqlite_path)
    src_cursor = src_conn.cursor()
    
    total_target_rows = 0
    validation_failed = False
    
    for table_name in source_schema:
        src_cursor.execute(f"SELECT COUNT(*) FROM \"{table_name}\"")
        src_count = src_cursor.fetchone()[0]
        
        try:
            quoted_table = generator.quote_ident(table_name)
            res, _, err = db.execute(f"SELECT COUNT(*) as count FROM {quoted_table}")
            if err: raise RuntimeError(err)
            target_count = list(res[0].values())[0]
            total_target_rows += target_count
            
            if src_count != target_count:
                print(f"FAILED: Row count mismatch for {table_name} in {args.target_db_name}: source={src_count}, target={target_count}", file=sys.stderr)
                validation_failed = True
        except Exception as e:
            print(f"FAILED: Error counting rows in {table_name} of {args.target_db_name}: {e}", file=sys.stderr)
            sys.exit(1)

    src_conn.close()
    
    if validation_failed:
        # If verify failed but total rows in target is 0, it means schema exists but data is empty.
        # This is safe to re-migrate (Exit Code 3)
        if total_target_rows == 0:
            print(f"EMPTY_DATA: Schema valid but no data in {args.target_db_name}.", file=sys.stderr)
            sys.exit(3)
        sys.exit(1)

    sys.exit(0)

if __name__ == "__main__":
    check_status()
