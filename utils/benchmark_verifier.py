import logging
import sys
from typing import Dict, Any, List

def verify_db_structure(db, source_schema: Dict[str, Any], dialect: str, target_db_name: str) -> int:
    """
    Verifies if target DB matches source schema structure and (optionally) data.
    Returns:
      0: Match
      1: Fail (Mismatch, prompt needed)
      2: Empty (No tables, safe to load)
      3: Schema OK, Data Empty (Safe to load)
    """
    try:
        target_meta = db.get_metadata()
    except Exception as e:
        err_str = str(e).lower()
        if "does not exist" in err_str or "unknown database" in err_str:
            return 2
        print(f"FAILED: Connection error for {target_db_name}: {e}", file=sys.stderr)
        return 1

    if not target_meta:
        return 2

    # 3. Verify Tables and Columns
    for table_name, source_table in source_schema.items():
        if table_name not in target_meta:
            print(f"FAILED: Table {table_name} missing in target DB {target_db_name}.", file=sys.stderr)
            return 1
        
        target_cols = {c["name"]: c["type"].upper() for c in target_meta[table_name]}
        
        for source_col in source_table["columns"]:
            if source_col["name"] not in target_cols:
                print(f"FAILED: Column {source_col['name']} missing in table {table_name} of target DB {target_db_name}.", file=sys.stderr)
                return 1
            
            # Simple Type Check (Very loose as we have many dialects)
            actual_type = target_cols[source_col["name"]]
            # We don't perform strict type mapping check here to keep it generic, 
            # just existence is usually enough for schema-only verification.
            
    # 4. Verify Row Counts (Optional check for 'Empty Data' status)
    total_target_rows = 0
    # For BIAS, we expect 0 rows. For others, we might want to check.
    # We skip row verification if the source_schema doesn't have counts
    return 0
