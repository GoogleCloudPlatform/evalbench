import logging
from unittest import result
from .db import DB
from google.cloud import bigtable
from google.cloud.bigtable import row_filters
from google.cloud.bigtable.row import DirectRow
from .util import (
    get_db_secret,
    with_cache_execute,
    DatabaseSchema,
)
from util.rate_limit import rate_limit, ResourceExhaustedError
from typing import Any, List, Optional, Tuple
import sqlglot
from sqlglot import exp
from google.cloud.bigtable.data import BigtableDataClient

COLUMN_FAMILY_TYPE = "ColumnFamily"


class BigtableDB(DB):
    def __init__(self, db_config):
        super().__init__(db_config)

        # admin client
        self.client: bigtable.Client = bigtable.Client(
            project=db_config["gcp_project_id"], admin=True
        )
        self.instance = self.client.instance(db_config["instance_id"])

        # data client for executing queries
        self.data_client = BigtableDataClient(
            project=db_config["gcp_project_id"], admin=True
        )

    def close_connections(self):
        self.data_client.close()
        self.client.close()

    def batch_execute(self, commands: list[str]):
        for command in commands:
            _, _, error = self.execute(command)
            if error:
                raise RuntimeError(f"{error}")

    def execute(
        self,
        query: str,
        eval_query: Optional[str] = None,
        use_cache=False,
        rollback=False,
    ) -> Tuple[Any, Any, Any]:
        if query.strip() == "":
            return None, None, None
        # Note: BT Does not support cache or rollback
        return self._execute(query, eval_query)

    def _execute_query(self, query: str) -> Tuple[List, Optional[str]]:
        # Helper function to execute a query and return results and error

        error = None
        result: List = []
        try:
            execute_query_iterator = self.data_client.execute_query(
                query=query, instance_id=self.instance.instance_id
            )
            for row in execute_query_iterator:
                result.append(dict(row.fields))
        except Exception as e:
            error = str(e)

        return result, error

    def _execute(
        self, query: str, eval_query: Optional[str] = None
    ) -> Tuple[Any, Any, Any]:
        def _run_execute(query: str, eval_query: Optional[str] = None):
            # execute the query and eval_query if provided
            result, error = self._execute_query(query)
            # if eval_query is provided, execute it and return its result and error
            if eval_query:
                eval_result, eval_error = self._execute_query(eval_query)
                return result, eval_result, error or eval_error
            # if no eval_query, return the main result and None for eval_result
            else:
                return result, None, error

        try:
            return rate_limit(
                (query, eval_query),
                _run_execute,
                self.execs_per_minute,
                self.semaphore,
                self.max_attempts,
            )
        except ResourceExhaustedError as e:
            logging.error(f"Rate limit exceeded on Bigtable: {e}")
            return None, None, str(e)

    def get_metadata(self) -> dict:
        # Bigtable is schemaless, but we can return column families
        db_metadata = {}
        tables = self.instance.list_tables()
        for table in tables:
            try:
                column_families = table.list_column_families()
                db_metadata[table.table_id] = [
                    {"name": cf, "type": COLUMN_FAMILY_TYPE} for cf in column_families
                ]
            except Exception:
                logging.error(f"Failed to get metadata for table {table.table_id}")
        return db_metadata

    def generate_ddl(
        self,
        schema: DatabaseSchema,
    ) -> list[str]:
        # Not applicable for Bigtable
        return []

    def create_tmp_database(self, database_name: str):
        # Not applicable for Bigtable
        pass

    def drop_tmp_database(self, database_name: str):
        # Not applicable for Bigtable
        pass

    def drop_all_tables(self):
        # Not applicable for Bigtable
        pass

    def insert_data(
        self, data: dict[str, List[str]], setup: Optional[List[str]] = None
    ):
        if not data:
            return
        if not data:
            return
        for table_name in data:
            for row in data[table_name]:
                print(row)

    def create_tmp_users(self, dql_user: str, dml_user: str, tmp_password: str):
        # Not applicable for Bigtable
        pass

    def delete_tmp_user(self, username: str):
        # Not applicable for Bigtable
        pass

    def _execute_auto_commit(self, query: str):
        # Not applicable for Bigtable
        return True, None
