from sqlalchemy.pool import NullPool
import sqlalchemy
from sqlalchemy import text, MetaData
import logging
from .db import DB
from google.cloud.sql.connector import Connector
from .util import (
    get_db_secret,
    with_cache_execute,
    DatabaseSchema,
)
from util.rate_limit import rate_limit, ResourceExhaustedError
from typing import Any, List, Optional, Tuple

DROP_ALL_TABLES_QUERY = """
USE master;
ALTER DATABASE {DATABASE} SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
DROP DATABASE {DATABASE};
CREATE DATABASE {DATABASE};
USE {DATABASE};
"""

DELETE_USER_QUERY = """
IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '{USERNAME}')
    DROP USER [{USERNAME}];

IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = '{USERNAME}')
    DROP LOGIN [{USERNAME}];
"""

CREATE_USERS_QUERY = """
CREATE LOGIN [{DQL_USERNAME}] WITH PASSWORD = '{PASSWORD}';
CREATE USER [{DQL_USERNAME}] FOR LOGIN [{DQL_USERNAME}];
GRANT SELECT ON DATABASE::{DATABASE} TO [{DQL_USERNAME}];

CREATE LOGIN [{DML_USERNAME}] WITH PASSWORD = '{PASSWORD}';
CREATE USER [{DML_USERNAME}] FOR LOGIN [{DML_USERNAME}];
GRANT SELECT, INSERT, UPDATE, DELETE ON DATABASE::{DATABASE} TO [{DML_USERNAME}];
"""


class SQLServerDB(DB):

    #####################################################
    #####################################################
    # Database Connection Setup Logic
    #####################################################
    #####################################################

    def __init__(self, db_config):
        super().__init__(db_config)
        logging.getLogger("pytds").setLevel(logging.ERROR)
        self.connector = Connector()

        def get_conn():
            conn = self.connector.connect(
                self.db_path,
                "pytds",
                user=self.username,
                password=self.password,
                db=self.db_name,
            )
            return conn

        def get_engine_args():
            common_args = {
                "creator": get_conn,
                "connect_args": {"command_timeout": 60, "multi_statements": True},
                "echo": False,
                "logging_name": None,
            }
            if "is_tmp_db" in db_config:
                common_args["poolclass"] = NullPool
            else:
                common_args["pool_size"] = 50
                common_args["pool_recycle"] = 300
            return common_args

        self.engine = sqlalchemy.create_engine("mssql+pytds://", **get_engine_args())

    def close_connections(self):
        try:
            self.engine.dispose()
            self.connector.close()
        except Exception:
            logging.warning(
                f"Failed to close connections. This may result in idle unused connections."
            )

    #####################################################
    #####################################################
    # Database Specific Execution Logic and Handling
    #####################################################
    #####################################################

    def batch_execute(self, commands: list[str]):
        _, _, error = self.execute(";\n".join(commands))
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
        self,
        query: str,
        eval_query: Optional[str] = None,
        rollback=False,
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
                if "57P03" in error:
                    raise ResourceExhaustedError("DB Exhausted") from e
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
                metadata.reflect(bind=connection, schema="dbo")
                for table in metadata.tables.values():
                    columns = []
                    for column in table.columns:
                        columns.append({"name": column.name, "type": str(column.type)})
                    db_metadata[table.name] = columns
        except Exception:
            pass

        return db_metadata

    #####################################################
    #####################################################
    # Setup / Teardown of temporary databases
    #####################################################
    #####################################################

    def generate_ddl(
        self,
        schema: DatabaseSchema,
    ) -> list[str]:
        create_statements = []
        for table in schema.tables:
            columns = ", ".join(
                [f"{column.name} {column.type}" for column in table.columns]
            )
            create_statements.append(f"CREATE TABLE dbo.{table.name} ({columns});")
        return create_statements

    def create_tmp_database(self, database_name: str):
        _, error = self._execute_autocommit(f"CREATE DATABASE {database_name};")
        if error:
            raise RuntimeError(f"Could not create database: {error}")
        self.tmp_dbs.append(database_name)

    def drop_tmp_database(self, database_name: str):
        if database_name in self.tmp_dbs:
            self.tmp_dbs.remove(database_name)
        _, error = self._execute_autocommit(f"DROP DATABASE {database_name};")
        if error:
            logging.info(f"Could not delete database: {error}")

    def drop_all_tables(self):
        _, error = self._execute_autocommit(DROP_ALL_TABLES_QUERY.format(DATABASE=self.db_name))
        if error:
            raise RuntimeError(error)

    def insert_data(self, data):
        if not data:
            return

        insertion_statements = []
        for table_name in data:
            for row in data[table_name]:
                formatted_values = []
                for value in row:
                    if str(value).lower() in ["true", "false"]:
                        formatted_values.append("1" if str(value).lower() == "true" else "0")
                    else:
                        formatted_values.append(str(value))

                inline_values = ", ".join(formatted_values)
                insertion_statements.append(f"INSERT INTO dbo.{table_name} VALUES ({inline_values});")

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
        try:
            self.batch_execute(
                CREATE_USERS_QUERY.format(
                    DQL_USERNAME=dql_user,
                    DML_USERNAME=dml_user,
                    PASSWORD=tmp_password,
                    DATABASE=self.db_name,
                ).split(";")
            )
        except RuntimeError as error:
            raise RuntimeError(f"Could not setup users. {error}")

    def delete_tmp_user(self, username: str):
        if username in self.tmp_users:
            self.tmp_users.remove(username)
        _, _, error = self.execute(DELETE_USER_QUERY.format(USERNAME=username))
        if error:
            logging.info(f"Could not delete tmp user due to {error}")

    #####################################################
    #####################################################
    # Internal helpers
    #####################################################
    #####################################################

    def _execute_autocommit(self, query: str):
        error = None
        raw_conn = None
        cursor = None
        try:
            raw_conn = self.engine.raw_connection()
            raw_conn.connection.autocommit = True # type: ignore
            cursor = raw_conn.cursor()
            cursor.execute(query)

        except Exception as e:
            error = str(e)

        finally:
            if cursor:
                cursor.close()
            if raw_conn:
                raw_conn.close()

        return error is None, error
