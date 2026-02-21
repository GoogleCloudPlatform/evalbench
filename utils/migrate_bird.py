import os
import sys
import argparse
import logging
from typing import Dict, Any, List

# Disable Spanner OpenTelemetry metrics to prevent exit crashes
os.environ["GOOGLE_CLOUD_DISABLE_OPENTELEMETRY"] = "true"

# Ensure evalbench is in path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "evalbench"))

from evalbench.databases import get_database
from utils.bird_migration.schema_extractor import extract_schema
from utils.bird_migration.ddl_generator import DDLGenerator
from utils.bird_migration.data_loader import DataLoader

def main():
    parser = argparse.ArgumentParser(description="Migrate BIRD SQLite to target DB")
    parser.add_argument("--sqlite_path", required=True)
    parser.add_argument("--db_config", required=True)
    parser.add_argument("--target_db_name", required=True)
    args = parser.parse_args()

    print(f"DEBUG ARGV: {sys.argv}")

    # 1. Extract Schema
    print(f"Extracting schema from {args.sqlite_path}...")
    schema = extract_schema(args.sqlite_path)
    print(f"Found {len(schema)} tables.")

    # 2. Connect to Target
    from util.config import load_yaml_config
    db_config = load_yaml_config(args.db_config)
    db_config["database_name"] = args.target_db_name
    
    print(f"Connecting to target DB using {args.db_config}...")
    dialect_name = "spanner_gsql" if "spanner_gsql" in args.db_config else \
                   "spanner_pg" if "spanner_pg" in args.db_config else \
                   "postgres" if "postgres" in args.db_config else \
                   "mysql" if "mysql" in args.db_config else "sqlite"
    
    print(f"DEBUG: Loaded config type: {db_config.get('dialect', 'unknown')}")
    
    db_wrapper = get_database(db_config, None)
    
    # 3. Cleanup and Setup
    print(f"Cleaning up target DB '{args.target_db_name}'...")
    print("Dropping existing tables (this may take a moment if there are locks)...")
    db_wrapper.drop_all_tables()
    print("Cleanup complete.")
    
    # 4. Generate and Apply DDL
    generator = DDLGenerator(dialect_name)
    ddl_statements = generator.generate(schema)
    
    print(f"Applying DDL ({len(ddl_statements)} statements)...")
    if "spanner" in dialect_name:
        # Use optimized batch DDL for Spanner
        db_wrapper.batch_execute(ddl_statements)
    else:
        # Use standard cursor for local DBs
        conn = db_wrapper.engine.raw_connection()
        cursor = conn.cursor()
        for i, stmt in enumerate(ddl_statements):
            if i % 5 == 0:
                print(f"  .. Executed {i}/{len(ddl_statements)} statements")
            cursor.execute(stmt)
        conn.commit()
        cursor.close()
        conn.close()
    print("DDL Applied.")

    # 5. Load Data
    print("Starting Data Loading...")
    loader = DataLoader(args.sqlite_path, db_wrapper, dialect_name)
    loader.load(schema)

    print("Migration Complete.")

if __name__ == "__main__":
    main()
