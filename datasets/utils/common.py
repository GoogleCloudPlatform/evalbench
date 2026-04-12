from enum import Enum
from google.cloud.bigtable.row import DirectRow
from google.cloud.bigtable.batcher import MutationsBatcher
import hashlib
from google.cloud.bigtable_admin_v2 import LogicalView
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud.bigtable.table import Table
from google.cloud.bigtable.row_data import PartialRowsData

DEFAULT_COLUMN_FAMILY = "columns"


def bigtable_table_id(table_id: str) -> str:
    return table_id + "-bt"


class TableOp(str, Enum):
    REBUILD = "REBUILD"
    DELETE_ONLY = "DELETE_ONLY"
    NO_ACTION = "NO_ACTION"


class LogicalViewType(str, Enum):
    TYPED = "TYPED"
    UNTYPED = "UNTYPED"


class LogicalViewBuilder:
    def __init__(
        self,
        instance_admin_client,
        gcp_project_id,
        instance_id,
        from_table,
        columns,
        logical_view_id,
    ):
        self.instance_admin_client = instance_admin_client
        self.gcp_project_id = gcp_project_id
        self.instance_id = instance_id
        self.from_table = from_table
        self.columns = columns
        self.logical_view_id = logical_view_id

    def query(self) -> str:
        raise NotImplementedError()

    def parent(self) -> str:
        return f"projects/{self.gcp_project_id}/instances/{self.instance_id}"

    def name(self) -> str:
        return self.parent() + f"/logicalViews/{self.logical_view_id}"

    def test_connection(self):
        self.instance_admin_client.get_logical_view(name=self.name())

    def delete(self):
        print(f"Deleting logical view: {self.logical_view_id}...")
        try:
            self.instance_admin_client.delete_logical_view(name=self.name())
            print(f"Deleted logical view: {self.logical_view_id}")
        except Exception as e:
            print(f"Logical view {self.logical_view_id} deletion exception: {e}")

    def build(self):
        logical_view = LogicalView()
        logical_view.name = self.name()
        logical_view.query = self.query()

        print(f"Creating logical view: {self.logical_view_id}...")
        self.instance_admin_client.create_logical_view(
            parent=self.parent(),
            logical_view_id=self.logical_view_id,
            logical_view=logical_view,
        )
        print(f"Created logical view: {self.logical_view_id}")

        self.test_connection()


class TypedLogicalViewBuilder(LogicalViewBuilder):
    def query(self):
        query_string = "SELECT "
        for col_name in self.columns.keys():
            sanitized_col_name = "".join(
                [c for c in col_name if c.isalnum() or c == "_"]
            )
            cast_col = f'CAST({DEFAULT_COLUMN_FAMILY}["{col_name}"] AS STRING)'
            col_type = self.columns[col_name]
            if col_type and col_type.upper() in (
                "INTEGER",
                "REAL",
                "INT",
                "NUMERIC",
                "FLOAT",
                "DOUBLE",
            ):
                cast_col = f"CAST({cast_col} AS FLOAT64)"
            part = f"{cast_col} AS `{sanitized_col_name}`, "
            query_string += part
        query_string = query_string[:-2]
        query_string += f" FROM `{self.from_table}`"
        return query_string


class UntypedLogicalViewBuilder(LogicalViewBuilder):
    def query(self):
        query_string = "SELECT "
        for col_name in self.columns.keys():
            sanitized_col_name = "".join([c for c in col_name if c.isalnum()])
            part = f'{DEFAULT_COLUMN_FAMILY}["{col_name}"] AS `{sanitized_col_name}`, '
            query_string += part
        query_string = query_string[:-2]
        query_string += f" FROM `{self.from_table}`"
        return query_string


class BigtableTableBuilder:
    def __init__(
        self,
        instance_admin_client,
        instance,
        table_id,
        columns,
        gcp_project_id,
        instance_id,
    ):
        self.instance_admin_client = instance_admin_client
        self.gcp_project_id = gcp_project_id
        self.instance_id = instance_id
        self.backing_table_id = bigtable_table_id(table_id)

        self.table = Table(self.backing_table_id, instance)
        self.columns = {col: columns.get(col, "") for col in columns.keys()}

    def delete(self):
        print(f"Deleting Bigtable table: {self.backing_table_id}...")
        self.table.delete()
        print("Deleted", self.table.name)

    def create(self):
        print(f"Creating Bigtable table: {self.backing_table_id}...")
        self.table.create()
        self.table.column_family(DEFAULT_COLUMN_FAMILY).create()
        print(f"Created Bigtable table: {self.backing_table_id}")

    def test_connection(self):
        if not self.table.exists():

            raise NotFound(f"Table {self.backing_table_id} does not exist.")

    def insert_rows(self, rows: list):
        mutations_batcher: MutationsBatcher = self.table.mutations_batcher()
        print("Inserting", len(rows), "rows.")
        row_count = 0
        for row in rows:
            row_key_elements = []
            for i, col_name in enumerate(self.columns):
                row_key_elements.append(f"#{col_name}#{row[i]}")
            long_row_key = "".join(row_key_elements)
            row_key = hashlib.sha256(long_row_key.encode("utf-8")).hexdigest()

            direct_row: DirectRow = self.table.direct_row(row_key.encode("utf-8"))
            for i, col_name in enumerate(self.columns):
                value = row[i]
                if value is None:
                    continue
                direct_row.set_cell(
                    DEFAULT_COLUMN_FAMILY, col_name, str(value).encode("utf-8")
                )
            mutations_batcher.mutate(direct_row)

            row_count += 1
            if row_count % 200 == 0:
                print("Inserted ", row_count, "rows.")
        mutations_batcher.flush()

    def insert_rows_if_empty(self, rows: list):
        # Check if the table is empty
        partial_row_data: PartialRowsData = self.table.read_rows()
        partial_row_data.consume_all(max_loops=1)
        if len(partial_row_data.rows) == 0:
            self.insert_rows(rows)
        else:
            print("Table is not empty, skipping row insertion.")


def build_bigtable_resources(
    bt_table: BigtableTableBuilder, logical_view: LogicalViewBuilder, table_op: TableOp
):
    if table_op == TableOp.REBUILD:
        logical_view.delete()
        try:
            bt_table.delete()
        except Exception as e:
            print(f"Table {bt_table.backing_table_id} deletion exception: {e}")
        try:
            bt_table.create()
        except AlreadyExists:
            print("Table already exists:", bt_table.backing_table_id)
        # sanity check to ensure that the table is created
        bt_table.test_connection()
        logical_view.build()
    elif table_op == TableOp.DELETE_ONLY:
        logical_view.delete()
        try:
            bt_table.delete()
        except NotFound:
            print(f"Table {bt_table.backing_table_id} not found, skipping deletion.")
    elif table_op == TableOp.NO_ACTION:
        print("TableOp is NO_ACTION. No operations will be performed.")
