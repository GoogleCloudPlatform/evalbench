from sqlalchemy.pool import NullPool
import sqlalchemy
from sqlalchemy import text, MetaData
from sqlalchemy.engine.base import Connection
import logging
from .db import DB
from google.cloud import spanner
from google.cloud.spanner_admin_database_v1.types import spanner_database_admin

from .util import (
    get_db_secret,
    with_cache_execute,
    DatabaseSchema,
)
from util.rate_limit import rate_limit, ResourceExhaustedError
from typing import Any, List, Optional, Tuple


class SpannerDB(DB):
    def __init__(self, db_config):
        super().__init__(db_config)
        # native Cloud Spanner API setup
        if "deployment" in db_config and db_config["deployment"] == "staging":
            # Prod
            self.spanner_api_endpoint = "staging-wrenchworks.sandbox.googleapis.com:443"
        else:
            # Staging
            self.spanner_api_endpoint = "spanner.googleapis.com"
        client = spanner.Client(
            project=db_config["gcp_project_id"],
            client_options={"api_endpoint": self.spanner_api_endpoint},
        )
        self.database_admin_api = client.database_admin_api
        spanner_instance = client.instance(db_config["instance_id"])
        self.database = spanner_instance.database(db_config["database_name"])
        # 2 sessions: one for operations, and one for anything else.
        self.pool = spanner.FixedSizePool(size=2, default_timeout=10)
        self.pool.bind(self.database)
        # sqlalchemy setup (for convenient, non-performance-critical ops)
        db_con_string = (
            f"spanner+spanner:///projects/{db_config['gcp_project_id']}/instances/"
            f"{db_config['instance_id']}/databases/{db_config['database_name']}"
        )
        self.engine = sqlalchemy.create_engine(
            db_con_string, connect_args={"client": client}, pool_pre_ping=True
        )

    def close_connections(self):
        try:
            self.engine.dispose()
        except Exception:
            logging.warning(
                f"Failed to close connections. This may result in idle unused connections."
            )

    def batch_execute(self, commands: list[str]):
        _, _, error = self.execute("\n".join(commands))
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
        if not use_cache or not self.cache_client or eval_query:
            return self._execute(query, eval_query, rollback)
        return with_cache_execute(
            query,
            self.engine.url,
            self._execute,
            self.cache_client,
        )

    def _execute(
        self, query: str, eval_query: Optional[str] = None, rollback=False
    ) -> Tuple[Any, Any, Any]:
        def _run_execute(query: str, eval_query: Optional[str] = None, rollback=False):
            result: List = []
            eval_result: List = []
            error = None
            try:
                with self.engine.connect() as connection:
                    with connection.begin() as transaction:
                        resultset = connection.execute(text(query))
                        if resultset.returns_rows:
                            rows = resultset.fetchall()
                            result.extend(r._asdict() for r in rows)

                        if eval_query:
                            eval_resultset = connection.execute(text(eval_query))
                            if eval_resultset.returns_rows:
                                eval_rows = eval_resultset.fetchall()
                                eval_result.extend(r._asdict() for r in eval_rows)

                        if rollback:
                            transaction.rollback()
            except Exception as e:
                error = str(e)
            return result, eval_result, error

        try:
            return rate_limit(
                (query, eval_query, rollback),
                _run_execute,
                self.execs_per_minute,
                self.semaphore,
                self.max_attempts,
            )
        except ResourceExhaustedError as e:
            logging.info(
                "Resource Exhausted on Postgres DB. Giving up execution. Try reducing execs_per_minute."
            )
            return None, None, None

    def get_metadata(self) -> dict:
        db_metadata = {}
        try:
            metadata_reflected_all = MetaData()
            metadata_reflected_all.reflect(bind=self.engine)
            for table in metadata_reflected_all.tables.values():
                columns = []
                for column in table.columns:
                    columns.append({"name": column.name, "type": str(column.type)})
                db_metadata[table.name] = columns
        except Exception:
            logging.error(f"Failed to get metadata")
        return db_metadata

    def generate_ddl(
        self,
        schema: DatabaseSchema,
    ) -> list[str]:
        create_statements = []
        for table in schema.tables:
            columns = ", ".join(
                [f"{column.name} {column.type}" for column in table.columns]
            )
            create_statements.append(f"CREATE TABLE public.{table.name} ({columns});")
        return create_statements

    def create_tmp_database(self, database_name: str):
        pass

    def drop_tmp_database(self, database_name: str):
        pass

    def drop_all_tables(self):
        _, _, error = self.execute(DROP_ALL_TABLES_QUERY)
        if error:
            raise RuntimeError(error)

    def insert_data(
        self, data: dict[str, List[str]], setup: Optional[List[str]] = None
    ):
        if not data:
            return
        for table_name in data:
            for row in data[table_name]:
                print(row)

    def create_tmp_users(self, dql_user: str, dml_user: str, tmp_password: str):
        pass

    def delete_tmp_user(self, username: str):
        pass

    def _execute_auto_commit(self, query: str):
        error = None
        try:
            with self.engine.connect() as connection:
                connection.execution_options(isolation_level="AUTOCOMMIT").execute(
                    text(query)
                )
        except Exception as e:
            error = str(e)
        return error is None, error
