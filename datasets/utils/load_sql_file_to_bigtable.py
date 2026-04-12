import argparse
import glob
import os
import re
from pathlib import Path

from common import (
    TableOp,
    LogicalViewType,
    LogicalViewBuilder,
    TypedLogicalViewBuilder,
    UntypedLogicalViewBuilder,
    BigtableTableBuilder,
    build_bigtable_resources,
)
from google.cloud import bigtable
from google.cloud.bigtable_v2.services.bigtable.client import BigtableClient
from google.api_core.exceptions import NotFound


def parse_sql_file(sql_path: str):
    """Stream-parse a PostgreSQL dump SQL file for CREATE TABLE and COPY ... FROM stdin blocks.

    Returns:
      schema: dict table_name -> list of (col_name, col_type)
      data: dict table_name -> {'columns': [...], 'rows': generator}
    """
    schema = {}
    data = {}
    create_table_re = re.compile(
        r"^CREATE TABLE\s+public\.([\"']?)(?P<table>[^\s(\"']+)\1\s*\((?P<body>.*)",
        re.I,
    )
    end_create_re = re.compile(r"^\);\s*$")
    copy_re = re.compile(
        r"^COPY\s+public\.([\"']?)(?P<table>[^\s(\"']+)\1\s*\((?P<cols>[^)]+)\)\s+FROM\s+stdin;",
        re.I,
    )
    with open(sql_path, encoding="utf-8") as f:
        in_create = False
        create_lines = []
        table_name = None
        # For COPY
        in_copy = False
        copy_table = None
        copy_cols = None
        copy_rows = []
        for line in f:
            if not in_create and not in_copy:
                m = create_table_re.match(line)
                if m:
                    in_create = True
                    table_name = m.group("table").strip('"').lower()
                    create_lines = [line]
                    continue
                m = copy_re.match(line)
                if m:
                    in_copy = True
                    copy_table = m.group("table").strip('"').lower()
                    copy_cols = [
                        c.strip().strip('"') for c in m.group("cols").split(",")
                    ]
                    copy_rows = []
                    continue
            if in_create:
                create_lines.append(line)
                if end_create_re.match(line):
                    # Parse columns
                    body = "".join(create_lines)
                    # Extract column lines between first '(' and last ')'
                    body_inner = re.search(r"\((.*)\)", body, re.S)
                    cols = []
                    if body_inner:
                        for col_line in body_inner.group(1).splitlines():
                            col_line = col_line.strip().rstrip(",")
                            if (
                                not col_line
                                or col_line.upper().startswith("CONSTRAINT")
                                or col_line.upper().startswith("PRIMARY KEY")
                            ):
                                continue
                            cm = re.match(
                                r'"?(?P<name>[^"\s]+)"?\s+(?P<type>.+)', col_line
                            )
                            if not cm:
                                continue
                            name = cm.group("name")
                            type_part = cm.group("type").strip()
                            type_part = re.split(
                                r"\s+(NOT NULL|DEFAULT|NULL|UNIQUE|CHECK|REFERENCES)\b",
                                type_part,
                                flags=re.I,
                            )[0].strip()
                            cols.append((name, type_part))
                    if cols:
                        schema[table_name] = cols
                    in_create = False
                    table_name = None
                    create_lines = []
                continue
            if in_copy:
                row_line = line.strip()
                if row_line == "\\.":
                    # End of COPY block
                    def row_gen(rows):
                        for row in rows:
                            yield row

                    data[copy_table] = {
                        "columns": copy_cols,
                        "rows": row_gen(copy_rows),
                    }
                    in_copy = False
                    copy_table = None
                    copy_cols = None
                    copy_rows = []
                    continue
                if row_line:
                    parts = row_line.split("\t")
                    values = [None if p == "\\N" else p for p in parts]
                    copy_rows.append(tuple(values))
    return schema, data


def main():
    parser = argparse.ArgumentParser(description="Load a SQL dump into Bigtable.")
    # TODO: move gcp-specific arg parse to common.py
    parser.add_argument(
        "--sql_path",
        type=str,
        help="Path or glob for SQL dump(s), e.g. datasets/utils/Sample.sql",
    )
    parser.add_argument(
        "--gcp_project_id", type=str, default="cloud-db-nl2sql", help="GCP project ID."
    )
    parser.add_argument("--instance_id", type=str, default="evalbench-air-travel")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--tables",
        type=str,
        default=None,
        help="Comma-separated list of tables to load",
    )
    parser.add_argument(
        "--table_op",
        type=str,
        choices=[e.value for e in TableOp],
        default=TableOp.NO_ACTION.value,
    )
    parser.add_argument(
        "--view_op",
        type=str,
        choices=[e.value for e in LogicalViewType],
        default=LogicalViewType.TYPED.value,
        help="Create logical views by default (TYPED)",
    )
    args = parser.parse_args()

    sql_paths = [
        p for p in glob.glob(os.path.expanduser(args.sql_path)) if p.endswith(".sql")
    ]
    if not sql_paths:
        print(f"No SQL files found matching: {args.sql_path}")
        return

    allowed_tables = None
    if args.tables:
        allowed_tables = {t.strip() for t in args.tables.split(",")}

    admin_client: bigtable.Client = bigtable.Client(
        project=args.gcp_project_id, admin=True
    )
    data_client: BigtableClient = BigtableClient()
    instance = admin_client.instance(args.instance_id)

    for sql_path in sql_paths:
        print(f"--- Processing SQL file: {sql_path} ---")

        schema, data = parse_sql_file(sql_path)

        for table_name, cols in schema.items():
            if allowed_tables and table_name not in allowed_tables:
                continue

            print("Processing table:", table_name)

            bt_table = BigtableTableBuilder(
                admin_client.instance_admin_client,
                instance,
                table_name,
                {col_name: col_type for col_name, col_type in cols},
                gcp_project_id=args.gcp_project_id,
                instance_id=args.instance_id,
            )
            logical_view_builder_args = dict(
                instance_admin_client=admin_client.instance_admin_client,
                gcp_project_id=args.gcp_project_id,
                instance_id=args.instance_id,
                from_table=bt_table.backing_table_id,
                columns={col_name: col_type for col_name, col_type in cols},
            )

            view_builder: LogicalViewBuilder
            if args.view_op == LogicalViewType.UNTYPED:
                logical_view_id = table_name + "-untyped"
                view_builder = UntypedLogicalViewBuilder(
                    **logical_view_builder_args, logical_view_id=logical_view_id
                )
            else:
                logical_view_id = table_name
                view_builder = TypedLogicalViewBuilder(
                    **logical_view_builder_args, logical_view_id=logical_view_id
                )

            build_bigtable_resources(bt_table, view_builder, args.table_op)

            if table_name in data:
                row_gen = data[table_name]["rows"]
                rows = []
                count = 0
                limit = 100
                for row in row_gen:
                    if args.limit > 0 and count >= args.limit:
                        break
                    rows.append(row)
                    count += 1
                    if len(rows) >= limit:
                        print(
                            f"Inserting batch of {len(rows)} rows into Bigtable table: {table_name}"
                        )
                        bt_table.insert_rows_if_empty(rows)
                        rows = []
                        break
            else:
                print(f"No data found for table: {table_name}")


if __name__ == "__main__":
    main()
