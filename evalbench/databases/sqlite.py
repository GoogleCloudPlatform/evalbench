from sqlalchemy.pool import NullPool
import sqlalchemy
from sqlalchemy import text, MetaData
import logging
import sqlite3
import os
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
            self.db_path = db_config["database_name"]
            if not self.db_path.endswith(".db"):
                self.db_path += ".db"
            conn = sqlite3.connect(self.db_path)
            conn.autocommit = False
            return conn

        def get_engine_args():
            return {
                "creator": get_conn,
                "connect_args": {"timeout": 60},
                "echo": False,
                "poolclass": NullPool,
            }

        self.engine = sqlalchemy.create_engine("sqlite:///", **get_engine_args())

    def close_connections(self):
        try:
            self.engine.dispose()
        except Exception as e:
            logging.warning("Failed to close connections. This may result in idle unused connections.")

    #####################################################
    #####################################################
    # Database Specific Execution Logic and Handling
    #####################################################
    #####################################################

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
            self.db_path,
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
                        resultset = connection.execute(text(query))
                        if resultset.returns_rows:
                            rows = resultset.fetchall()
                            result.extend(r._asdict() for r in rows)

                        if eval_query:
                            eval_resultset = connection.execute(text(eval_query))
                            if eval_resultset.returns_rows:
                                eval_rows = eval_resultset.fetchall()
                                eval_result.extend(r._asdict() for r in eval_rows)

                        if batch_commands and len(batch_commands) > 0:
                            for command in batch_commands:
                                connection.execute(text(command))

                        if rollback:
                            transaction.rollback()
            except Exception as e:
                error = str(e)
                if "database is locked" in error:
                    raise ResourceExhaustedError("SQLite Database is locked, retry later") from e
                elif "disk I/O error" in error:
                    raise ResourceExhaustedError("Disk I/O error occurred, check storage") from e
            return result, eval_result, error

        return rate_limit(
            (query, eval_query, rollback),
            _run_execute,
            self.execs_per_minute,
            self.semaphore,
            self.max_attempts,
        )

    def get_metadata(self) -> dict:
        db_metadata = {}

        try:
            with self.engine.connect() as connection:
                metadata = MetaData()
                metadata.reflect(bind=connection)
                for table in metadata.tables.values():
                    columns = []
                    for column in table.columns:
                        columns.append({"name": column.name, "type": str(column.type)})
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
            columns = ", ".join([f"{column.name} {column.type}" for column in table.columns])
            create_statements.append(f"CREATE TABLE {table.name} ({columns});")
        return create_statements

    def create_tmp_database(self, database_name: str):
        try:
            if not database_name.endswith(".db"):
                db_path = f"{database_name}.db"
            else:
                db_path = database_name

            open(db_path, 'a').close()
        except Exception as error:
            raise RuntimeError(f"Could not create database: {error}")
        self.tmp_dbs.append(db_path)

    def drop_tmp_database(self, database_name: str):
        if database_name in self.tmp_dbs:
            self.tmp_dbs.remove(database_name)
        try:
            if os.path.exists(database_name):
                os.remove(database_name)
        except Exception as error:
            logging.error(f"Could not delete database: {error}")

    def drop_all_tables(self):
        try:
            result = self.execute(GET_TABLES_SQL)
            tables = [table['name'] for table in result[0]]

            if tables:
                drop_statements = [DROP_TABLE_SQL.format(TABLE=table) for table in tables]
                self.batch_execute(drop_statements)

        except Exception as error:
            logging.error(f"Failed to drop all tables: {error}")

    def insert_data(self, data: dict[str, List[str]]):
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
