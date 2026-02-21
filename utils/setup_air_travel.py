import os
import sys
import re
from typing import Dict, List, Any

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "evalbench"))

from evalbench.databases import get_database
from util.config import load_yaml_config
from utils.benchmark_verifier import verify_db_structure

TYPE_MAP = {
    "integer": {"postgres": "INTEGER", "mysql": "INTEGER", "spanner_gsql": "INT64", "spanner_pg": "bigint"},
    "text": {"postgres": "TEXT", "mysql": "TEXT", "spanner_gsql": "STRING(MAX)", "spanner_pg": "text"},
    "date": {"postgres": "DATE", "mysql": "DATE", "spanner_gsql": "DATE", "spanner_pg": "date"},
    "float": {"postgres": "DOUBLE PRECISION", "mysql": "DOUBLE", "spanner_gsql": "FLOAT64", "spanner_pg": "double precision"},
    "money": {"postgres": "NUMERIC", "mysql": "DECIMAL(19,4)", "spanner_gsql": "NUMERIC", "spanner_pg": "numeric"},
    "timestamp": {"postgres": "TIMESTAMP", "mysql": "DATETIME", "spanner_gsql": "TIMESTAMP", "spanner_pg": "timestamptz"},
    "boolean": {"postgres": "BOOLEAN", "mysql": "BOOLEAN", "spanner_gsql": "BOOL", "spanner_pg": "boolean"},
}

def parse_textproto(file_path):
    with open(file_path, 'r') as f:
        content = f.read()
    tables = []
    current_table = None
    lines = content.split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith('table:'):
            if current_table: tables.append(current_table)
            table_name = re.search(r'"([^"]+)"', line).group(1)
            current_table = {"name": table_name, "columns": []}
        elif line.startswith('column:'):
            col_name = re.search(r'"([^"]+)"', line).group(1)
            current_table["columns"].append({"name": col_name})
        elif line.startswith('data_type:'):
            dtype = re.search(r'"([^"]+)"', line).group(1)
            current_table["columns"][-1]["type"] = dtype
    if current_table: tables.append(current_table)
    return tables

def quote_ident(name, dialect):
    if dialect in ["postgres", "spanner_pg"]: return f'"{name}"'
    if dialect in ["mysql", "spanner_gsql"]: return f'`{name}`'
    return name

def generate_ddl(tables, dialect):
    ddl = []
    for table in tables:
        cols = []
        pks = []
        pk_found = False
        for col in table["columns"]:
            default_type = "STRING(MAX)" if dialect == "spanner_gsql" else "TEXT"
            ctype = TYPE_MAP.get(col["type"], {}).get(dialect, default_type)
            if dialect == "mysql" and (col["name"] == "id" or "_id" in col["name"] or "code" in col["name"]) and ctype == "TEXT":
                ctype = "VARCHAR(255)"
            quoted_col = quote_ident(col["name"], dialect)
            col_def = f"{quoted_col} {ctype}"
            if not pk_found and (col["name"] == "id" or "_id" in col["name"] or "code" in col["name"]):
                if "spanner" in dialect: col_def += " NOT NULL"
                pks.append(quoted_col)
                pk_found = True 
            cols.append(col_def)
        if "spanner" in dialect and not pk_found:
            pk_col = quote_ident("eb_row_id", dialect)
            cols.append(f"{pk_col} {'INT64' if dialect == 'spanner_gsql' else 'bigint'} NOT NULL")
            pks.append(pk_col)
        quoted_table = quote_ident(table['name'], dialect)
        create_stmt = f"CREATE TABLE {quoted_table} ({', '.join(cols)})"
        if pks:
            if dialect == "spanner_gsql": create_stmt += f" PRIMARY KEY ({', '.join(pks)})"
            else: create_stmt = f"CREATE TABLE {quoted_table} ({', '.join(cols)}, PRIMARY KEY ({', '.join(pks)}))"
        if dialect == "mysql": create_stmt += ";"
        ddl.append(create_stmt)
    return ddl

def setup_air_travel():
    print("Setting up Air Travel...")
    schema_path = "datasets/air_travel/schemas/air_travel/postgres.textproto"
    tables = parse_textproto(schema_path)
    source_schema = {t["name"]: t for t in tables}

    configs = [
        ("datasets/db_configs/postgres.yaml", "postgres", "air_travel"),
        ("datasets/db_configs/mysql.yaml", "mysql", "air_travel"),
        ("datasets/db_configs/spanner_gsql.yaml", "spanner_gsql", "air_travel_gsql"),
        ("datasets/db_configs/spanner_pg.yaml", "spanner_pg", "air_travel_pg")
    ]
    
    for config_path, dialect, db_name in configs:
        print(f"  Target: {db_name} ({dialect})")
        try:
            conf = load_yaml_config(config_path)
            conf["database_name"] = db_name
            db = get_database(conf, None)
            
            # 1. VERIFY
            res = verify_db_structure(db, source_schema, dialect, db_name)
            if res == 0:
                print("    [OK] Verified.")
                continue
            
            if res == 1:
                confirm = input(f"    [FAIL] Validation mismatch for {db_name}. DROP and recreate? [y/N] ")
                if confirm.lower() != 'y': continue
                print("    Dropping tables...")
                db.drop_all_tables()
            
            # 2. CREATE
            ddl_stmts = generate_ddl(tables, dialect)
            if "spanner" in dialect:
                db.batch_execute(ddl_stmts)
            else:
                conn = db.engine.raw_connection()
                cur = conn.cursor()
                for stmt in ddl_stmts: cur.execute(stmt)
                conn.commit()
                cur.close()
                conn.close()
            print("    DDL Applied.")
        except Exception as e:
            print(f"    FAILED: {e}")

if __name__ == "__main__":
    setup_air_travel()
