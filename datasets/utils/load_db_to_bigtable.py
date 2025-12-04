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

DEFAULT_COLUMN_FAMILY = "columns"

"""
One-time setup helper to load a sqllite database into Bigtable with ADK. 

Command from project root:

$ python ./datasets/utils/load_db_to_bigtable.py
--db_path=./db_connections/bird/.* 
--rebuild
--limit 10

Dryrun mode:
$ python ./datasets/utils/load_db_to_bigtable.py
--db_path=./db_connections/bird/.* 
--dry_run
--limit 10

"""


class BigtableRelationalTable:
    # A logical view with a hidden Bigtable table backing it to simulate a relational table

    columns: dict
    table_id: str
    backing_table_id: str

    table: Table
    logical_view: LogicalView
    instance_admin_client: BigtableInstanceAdminClient
    dry_run: bool

    def __init__(
        self,
        instance_admin_client: BigtableInstanceAdminClient,
        instance: Instance,
        table_id: str,
        columns: dict,
        gcp_project_id: str,
        instance_id: str,
        dry_run: bool,
    ):
        # initialize backing table
        self.instance_admin_client = instance_admin_client
        self.gcp_project_id = gcp_project_id
        self.instance_id = instance_id
        self.table_id = table_id  # this is actually the logical view id
        self.logical_view_name = (
            f"projects/{gcp_project_id}/instances/{instance_id}/logicalViews/{table_id}"
        )
        self.backing_table_id = table_id + "-bt"
        self.table = Table(self.backing_table_id, instance)
        self.columns = {}
        for col in columns.keys():
            self.columns[col] = columns[col]

        # create logical view to represent the table
        self.logical_view = LogicalView()
        query_string = "SELECT "
        for col_name in self.columns.keys():
            # sanitize the col to just an alphanumeric string
            sanitized_col_name = "".join([c for c in col_name if c.isalnum()])

            # Cast as type because the default is bytes - which we can't do operations on.
            cast_col = f'CAST({DEFAULT_COLUMN_FAMILY}["{col_name}"] AS STRING)'
            col_type = self.columns[col_name]
            if col_type in ("INTEGER", "REAL"):
                cast_col = f"CAST({cast_col} AS FLOAT64)"
            part = f"{cast_col} AS `{sanitized_col_name}`, "
            query_string += part
        query_string = query_string[:-2]  # remove last comma
        query_string += f" FROM `{self.backing_table_id}`"
        self.logical_view.query = query_string

        self.dry_run = dry_run

    def delete(self):
        # delete resources if they exist
        try:
            self.instance_admin_client.delete_logical_view(name=self.logical_view_name)
            print("Deleted", self.logical_view_name)
        except NotFound:
            pass

        try:
            self.table.delete()
            print("Deleted", self.table.name)
        except NotFound:
            pass

    def rebuild(self):
        if self.dry_run:
            print("Dry run mode, table to create:")
            print(self.table.name)
            print()
            print("Dry run mode, logical view to create:")
            print("View: ", self.logical_view)
            return

        self.delete()

        print("Rebuilding table", self.table.name)
        # create table
        self.table.create()
        self.table.column_family(DEFAULT_COLUMN_FAMILY).create()
        # create logical view

        print("Rebuilding logical view", self.logical_view_name)
        print("View: ", self.logical_view)
        self.instance_admin_client.create_logical_view(
            parent=f"projects/{self.gcp_project_id}/instances/{self.instance_id}",
            logical_view_id=self.table_id,
            logical_view=self.logical_view,
        )

    def test_connection(self) -> None | Exception:
        if self.dry_run:
            print("Dry run mode. Not testing connection to:")
            print("Table:", self.table.name)
            print("Logical view:", self.logical_view_name)
            return

        if not self.table.exists():
            raise NotFound(f"Table {self.backing_table_id} does not exist.")

        self.instance_admin_client.get_logical_view(
            name=f"projects/{self.gcp_project_id}/instances/{self.instance_id}/logicalViews/{self.table_id}"
        )

    def insert_rows(self, rows):
        if self.dry_run:
            print("Dry run mode, not inserting", len(rows), "rows.")
            return

        mutations_batcher: MutationsBatcher = self.table.mutations_batcher()
        print("Inserting", len(rows), "rows.")
        row_count = 0
        for row in rows:
            # deterministic row keys to prevent duplicate rows
            row_key_elements: list = []
            for i, col_name in enumerate(self.columns):
                row_key_elements.append(f"#{col_name}#{row[i]}")

            # hash rowkey to be less than 4096 bytes
            long_row_key: str = "".join(row_key_elements)
            row_key = hashlib.sha256(long_row_key.encode("utf-8")).hexdigest()

            direct_row: DirectRow = self.table.direct_row(row_key.encode("utf-8"))
            for i, col_name in enumerate(self.columns):
                value = row[i]
                if value is None:
                    continue  # skip nulls
                # Write into the column family (sanitized name) with qualifier b'value'
                direct_row.set_cell(
                    DEFAULT_COLUMN_FAMILY, col_name, str(value).encode("utf-8")
                )
            mutations_batcher.mutate(direct_row)

            row_count += 1
            if row_count % 200 == 0:
                print("Inserted ", row_count, "rows.")
        mutations_batcher.flush()


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
    parser.add_argument(
        "--rebuild",
        action="store_true",
        default=False,
        help="Whether to rebuild the Bigtable tables if they exist.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        default=False,
        help="Whether to delete the Bigtable tables.",
    )
    # explicitly look for tables, comma separated
    parser.add_argument(
        "--tables",
        type=str,
        default=None,
        help="A comma-separated list of tables to load.",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        default=False,
        help="Dry run mode - parse and print sqllite table schemas and equivalent Bigtable schemas but don't make any change.",
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
                for col, col_type in db_schema[table_id]:
                    print(f"  {col}: {col_type}")

            for tbl in db_schema.keys():
                if allowed_tables and tbl not in allowed_tables:
                    continue

                cols = db_schema[tbl]
                # Connect or create the bigtable object
                bt_table = BigtableRelationalTable(
                    admin_client.instance_admin_client,
                    instance,
                    tbl,
                    {col_name: col_type for col_name, col_type in cols},
                    gcp_project_id=args.gcp_project_id,
                    instance_id=args.instance_id,
                    dry_run=args.dry_run,
                )

                if args.delete:
                    bt_table.delete()
                    continue

                # (re)build resources if they exist
                elif args.rebuild:
                    bt_table.rebuild()

                bt_table.test_connection()

                # get and insert rows
                rows = get_rows_from_sqlite(cur, tbl, limit=args.limit)
                print(f"Fetched {len(rows)} rows from sqlite table", tbl)
                bt_table.insert_rows(rows)

                print("Inserted rows into Bigtable.")
