import argparse
import contextlib
import os
import sqlite3
import glob
from pathlib import Path
from google.cloud import bigtable
from google.cloud.bigtable.table import Table
from google.cloud.bigtable.instance import Instance
from google.cloud.bigtable_admin_v2 import BigtableInstanceAdminClient, LogicalView
from google.api_core.exceptions import NotFound
from google.cloud.bigtable.row import DirectRow
from google.cloud.bigtable.batcher import MutationsBatcher
from google.cloud.bigtable_v2.services.bigtable.client import BigtableClient
from uuid import uuid4
import hashlib
from abc import ABC, abstractmethod
from enum import Enum

from common import (
    TableOp,
    LogicalViewType,
    LogicalViewBuilder,
    TypedLogicalViewBuilder,
    UntypedLogicalViewBuilder,
    BigtableTableBuilder,
    build_bigtable_resources,
)


"""
One-time setup helper to load a sqlite database into Bigtable.

This script can:
- Rebuild Bigtable tables from sqlite tables (--table_op=REBUILD).
- Delete Bigtable tables (--table_op=DELETE_ONLY).
- Create typed or untyped logical views on top of Bigtable tables (--view_op).

Example commands from the project root:

# Rebuild all tables and create typed views for all sqlite dbs in a directory
$ python ./datasets/utils/load_sqlite_db_to_bigtable.py /
    --db_path="./path/to/your/databases/*.sqlite" /
    --table_op=REBUILD /
    --view_op=TYPED

# Only rebuild specific tables and create untyped views, with a row limit
$ python ./datasets/utils/load_sqlite_db_to_bigtable.py /
    --db_path="./path/to/your/database.sqlite" /
    --tables="table1,table2" /
    --table_op=REBUILD /
    --view_op=UNTYPED /
    --limit=1000

# Delete all bigtable tables associated with a database
$ python ./datasets/utils/load_sqlite_db_to_bigtable.py /
    --db_path="./path/to/your/database.sqlite" /
    --table_op=DELETE_ONLY

# "Dry run" - just print the schema of the sqlite databases without changing Bigtable
$ python ./datasets/utils/load_sqlite_db_to_bigtable.py /
    --db_path="./path/to/your/databases/*.sqlite"

"""


@contextlib.contextmanager
def get_db_cursor(db_path):
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        yield conn.cursor()
    finally:
        conn.close()


def get_all_tables_and_columns(cur: sqlite3.Cursor) -> dict:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = [row[0] for row in cur.fetchall()]
    db_schema = {}
    for table in tables:
        cur.execute(f'PRAGMA table_info("{table}")')
        columns = [(col[1], col[2]) for col in cur.fetchall()]  # (name, type)
        db_schema[table] = columns
    return db_schema


def get_rows_from_sqlite(cur: sqlite3.Cursor, table_name, limit) -> list:
    cur.execute(f"SELECT * FROM `{table_name}`")
    if limit > 0:
        rows = cur.fetchmany(limit)
    else:
        rows = cur.fetchall()
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load a sqlite database into Bigtable."
    )
    parser.add_argument(
        "--db_path", type=str, help="Path or glob pattern for the sqlite database(s)."
    )
    parser.add_argument(
        "--gcp_project_id", type=str, default="cloud-db-nl2sql", help="GCP project ID."
    )
    parser.add_argument(
        "--instance_id", type=str, default="evalbench", help="Bigtable instance ID."
    )
    parser.add_argument(
        "--table", type=str, default=None, help="Table name to inspect."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit the number of rows to fetch. Unprovided or a value of 0 means no limit.",
    )
    # explicitly look for tables, comma separated
    parser.add_argument(
        "--tables",
        type=str,
        default=None,
        help="A comma-separated list of tables to load.",
    )
    parser.add_argument(
        "--table_op",
        type=str,
        choices=[e.value for e in TableOp],
        default=TableOp.NO_ACTION.value,
        help="Choose a table operation, defaults to doing nothing.",
    )
    parser.add_argument(
        "--view_op",
        type=str,
        choices=[e.value for e in LogicalViewType],
        default=LogicalViewType.TYPED.value,
        help="Choose a view type.",
    )

    args = parser.parse_args()

    # Initialize Bigtable clients
    admin_client: bigtable.Client = bigtable.Client(
        project=args.gcp_project_id, admin=True
    )
    data_client: BigtableClient = BigtableClient()
    instance: Instance = admin_client.instance(args.instance_id)

    # filter by tables if the arg is provided.
    allowed_tables = None
    if args.tables:
        allowed_tables = {t.strip() for t in args.tables.split(",")}

    # get all .sqlite database file paths that match db_path regex
    db_paths = [
        path
        for path in glob.glob(os.path.expanduser(args.db_path))
        if path.endswith(".sqlite")
    ]

    if not db_paths:
        print(f"No databases found matching path: {args.db_path}")
        exit()
    print(f"Found {len(db_paths)} databases to load.")

    for db_path in db_paths:
        print(f"--- Processing database: {db_path} ---")
        with get_db_cursor(db_path=Path(db_path)) as cur:
            # fetch database schema
            db_schema = get_all_tables_and_columns(cur)

            print("Fetched database schema:")
            for table_id in db_schema.keys():
                print("Table:", table_id)
                columns = db_schema[table_id]
                count = 2
                for col, col_type in columns[:count]:
                    print(f"  {col}: {col_type}")
                if len(columns) > count:
                    print(f"  ... ({len(columns) - count} more columns)")
            print()

            for tbl in db_schema.keys():
                print("Processing table: ", tbl)

                if allowed_tables and tbl not in allowed_tables:
                    continue

                cols = db_schema[tbl]

                # View and table builders
                bt_table = BigtableTableBuilder(
                    admin_client.instance_admin_client,
                    instance,
                    tbl,
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

                # logical view has the same name as the sqlite table
                view_builder: LogicalViewBuilder
                if args.view_op == LogicalViewType.UNTYPED:
                    logical_view_id = tbl + "-untyped"
                    view_builder = UntypedLogicalViewBuilder(
                        **logical_view_builder_args, logical_view_id=logical_view_id
                    )
                else:  # args.view_op == LogicalViewType.TYPED
                    logical_view_id = tbl
                    view_builder = TypedLogicalViewBuilder(
                        **logical_view_builder_args, logical_view_id=logical_view_id
                    )

                build_bigtable_resources(bt_table, view_builder, args.table_op)

                rows = get_rows_from_sqlite(cur, tbl, limit=args.limit)
                print(f"Fetched {len(rows)} rows from sqlite table", tbl)
                bt_table.insert_rows_if_empty(rows)
                print("Inserted rows into Bigtable.")
