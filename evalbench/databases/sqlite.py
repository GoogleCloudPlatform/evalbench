from sqlalchemy.pool import NullPool
import sqlalchemy
from sqlalchemy import text, MetaData
from sqlalchemy.engine.base import Connection
import logging
import sqlite3
import os
import sqlparse
from .db import DB
from .util import (
    with_cache_execute,
    DatabaseSchema,
)
from util.rate_limit import rate_limit, ResourceExhaustedError
from typing import Any, List, Optional, Tuple

DROP_TABLE_SQL = "DROP TABLE {TABLE};"
GET_TABLES_SQL = "SELECT name FROM sqlite_schema WHERE type='table';"


class SQLiteDB(DB):

    #####################################################
    #####################################################
    # Database Connection Setup Logic
    #####################################################
    #####################################################

    def __init__(self, db_config):
        super().__init__(db_config)

        def get_conn():
            path = self._get_connection_path(self.db_path, self.db_name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            conn = sqlite3.connect(path)
            return conn

        def get_engine_args():
            return {
                "creator": get_conn,
                "connect_args": {"timeout": 60},
                "echo": False,
                "poolclass": NullPool,
            }

        self.engine = sqlalchemy.create_engine(
            "sqlite:///", **get_engine_args())

    def close_connections(self):
        try:
            self.engine.dispose()
        except Exception as e:
            logging.warning(
                "Failed to close connections. This may result in idle unused connections."
            )

    #####################################################
    #####################################################
    # Database Specific Execution Logic and Handling
    #####################################################
    #####################################################

    def _execute_queries(self, connection: Connection, query: str) -> List:
        result: List = []
        for sub_query in sqlparse.split(query):
            if sub_query:
                resultset = connection.execute(text(sub_query))
                if resultset.returns_rows:
                    rows = resultset.fetchall()
                    result.extend(r._asdict() for r in rows)
        return result

    def batch_execute(self, commands: list[str]):
        batch_commands = []
        for command in commands:
            if command.strip() != "":
                batch_commands.append(command)
        _, _, error = self._execute("SELECT 1;", batch_commands=batch_commands)
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
            self.db_name,
            self._execute,
            self.cache_client,
        )

    def _execute(
        self,
        query: str,
        eval_query: Optional[str] = None,
        rollback=False,
        batch_commands: list[str] = [],
    ) -> Tuple[Any, Any, Any]:
        def _run_execute(query: str, eval_query: Optional[str] = None, rollback=False):
            result: List = []
            eval_result: List = []
            error = None
            try:
                with self.engine.connect() as connection:
                    with connection.begin() as transaction:
                        result = self._execute_queries(connection, query)

                        if eval_query:
                            eval_result = self._execute_queries(
                                connection, eval_query)

                        if batch_commands and len(batch_commands) > 0:
                            for command in batch_commands:
                                connection.execute(text(command))

                        if rollback:
                            transaction.rollback()
            except Exception as e:
                error = str(e)
                if "database is locked" in error:
                    raise ResourceExhaustedError(
                        "SQLite Database is locked, retry later"
                    ) from e
                elif "disk I/O error" in error:
                    raise ResourceExhaustedError(
                        "Disk I/O error occurred, check storage"
                    ) from e
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
                "Resource Exhausted on SQLite DB. Giving up execution. Try reducing execs_per_minute."
            )
            return None, None, None

    def get_metadata(self) -> dict:
        db_metadata = {}

        try:
            with self.engine.connect() as connection:
                metadata = MetaData()
                metadata.reflect(bind=connection)
                for table in metadata.tables.values():
                    columns = []
                    for column in table.columns:
                        columns.append(
                            {"name": column.name, "type": str(column.type)})
                    db_metadata[table.name] = columns
        except Exception:
            pass

        return db_metadata

    # #####################################################
    # #####################################################
    # # Setup / Teardown of temporary databases
    # #####################################################
    # #####################################################

    def generate_ddl(
        self,
        schema: DatabaseSchema,
    ) -> list[str]:
        create_statements = []
        for table in schema.tables:
            columns = ", ".join(
                [f"{column.name} {column.type}" for column in table.columns]
            )
            create_statements.append(f"CREATE TABLE {table.name} ({columns});")
        return create_statements

    def create_tmp_database(self, database_name: str):
        try:
            target_path = self._get_connection_path(
                self.db_path, database_name)

            # Attempt to find source DB.
            # Strategy 1: Use self.db_name (config)
            source_candidates = [self.db_name]

            # Strategy 2: Infer from temp name (e.g. 'thrombosis_prediction_GUID' -> 'thrombosis_prediction')
            # BIRD DB names can have underscores, so we split from right.
            if "_" in database_name:
                # Split only last part (GUID)
                parts = database_name.rsplit("_", 1)
                source_candidates.append(parts[0])

            copied = False
            for src_name in source_candidates:
                source_path = self._get_connection_path(self.db_path, src_name)
                if os.path.exists(source_path) and os.path.getsize(source_path) > 0:
                    import shutil
                    shutil.copy2(source_path, target_path)
                    copied = True
                    break

            if not copied:
                open(target_path, "a").close()

        except Exception as error:
            raise RuntimeError(f"Could not create database: {error}")
        self.tmp_dbs.append(database_name)

    def drop_tmp_database(self, database_name: str):
        if database_name in self.tmp_dbs:
            self.tmp_dbs.remove(database_name)
        try:
            db_path = self._get_connection_path(self.db_path, database_name)
            if os.path.exists(db_path):
                os.remove(db_path)
        except Exception as error:
            logging.error(f"Could not delete database: {error}")

    def ensure_database_exists(self, database_name: str) -> None:
        import sqlite3
        import os
        filename = f"{self.db_path}/{database_name}{self.extension}"
        os.makedirs(self.db_path, exist_ok=True)
        conn = sqlite3.connect(filename)
        conn.close()

    def drop_all_tables(self):
        try:
            result = self.execute(GET_TABLES_SQL)
            tables = [table["name"] for table in result[0]]

            if tables:
                drop_statements = [
                    DROP_TABLE_SQL.format(TABLE=table) for table in tables
                ]
                self.batch_execute(drop_statements)

        except Exception as error:
            logging.error(f"Failed to drop all tables: {error}")

    def insert_data(self, data: dict[str, List[str]], setup: Optional[List[str]] = None):
        if not data:
            return
        insertion_statements = []
        for table_name in data:
            for row in data[table_name]:
                inline_columns = ", ".join([f"{value}" for value in row])
                insertion_statements.append(
                    f"INSERT INTO `{table_name}` VALUES ({inline_columns});"
                )
        try:
            self.batch_execute(insertion_statements)
        except RuntimeError as error:
            raise RuntimeError(f"Could not insert data into database: {error}")

    #####################################################
    #####################################################
    # Database User Management
    #####################################################
    #####################################################

    def create_tmp_users(self, dql_user: str, dml_user: str, tmp_password: str):
        pass

    def delete_tmp_user(self, username: str):
        pass

    def _get_connection_path(self, db_path, db_name):
        extension = self.extension
        if not db_name.endswith(extension):
            db_name = db_name + extension

        connection_path = os.path.join(db_path, f"{db_name}")
        return connection_path
