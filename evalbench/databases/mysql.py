import sqlalchemy
import sqlparse
from sqlalchemy import text, MetaData
from sqlalchemy.engine.base import Connection
import pymysql
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
DROP DATABASE {DATABASE};
CREATE DATABASE {DATABASE};
USE {DATABASE};
"""

DELETE_USER_QUERY = """
DROP USER IF EXISTS "{USERNAME}"@"%";
"""

CREATE_USERS_QUERY = """
CREATE USER IF NOT EXISTS "{DQL_USERNAME}"@"%" IDENTIFIED BY "{PASSWORD}";
GRANT USAGE ON *.* TO "{DQL_USERNAME}"@"%";
GRANT SELECT ON `{DATABASE}`.* TO "{DQL_USERNAME}"@"%";
GRANT SELECT ON mysql.* TO "{DQL_USERNAME}"@"%";
GRANT SHOW DATABASES ON *.* TO "{DQL_USERNAME}"@"%";
GRANT SHOW VIEW ON *.* TO "{DQL_USERNAME}"@"%";
CREATE USER IF NOT EXISTS "{DML_USERNAME}"@"%" IDENTIFIED BY "{PASSWORD}";
GRANT USAGE ON *.* TO "{DML_USERNAME}"@"%";
GRANT SELECT, INSERT, UPDATE, DELETE ON `{DATABASE}`.* TO "{DML_USERNAME}"@"%";
FLUSH PRIVILEGES;
"""


class MySQLDB(DB):

    #####################################################
    #####################################################
    # Database Connection Setup Logic
    #####################################################
    #####################################################

    def __init__(self, db_config):
        super().__init__(db_config)

        # Auto-deduce use_cloud_sql: format is PROJECT:REGION:INSTANCE (2 colons)
        self.use_cloud_sql = db_config.get("use_cloud_sql")
        if self.use_cloud_sql is None:
            self.use_cloud_sql = (self.db_path.count(":") == 2)

        self.connector = Connector() if self.use_cloud_sql else None

        def get_conn():
            """Callable for sqlalchemy 'creator' parameter."""
            if self.use_cloud_sql:
                return self.connector.connect(
                    self.db_path,
                    "pymysql",
                    user=self.username,
                    password=self.password,
                    db=self.db_name,
                )
            else:
                # Local/Direct connection
                host = self.db_path
                port = 3306
                if ":" in self.db_path:
                    parts = self.db_path.split(":")
                    host = parts[0]
                    port = int(parts[1])

                return pymysql.connect(
                    host=host,
                    port=port,
                    user=self.username,
                    password=self.password or "",
                    database=self.db_name
                )

        def get_engine_config():
            """Returns (db_url, engine_args)"""
            args = {
                "connect_args": {},
            }
            url = ""

            if self.use_cloud_sql:
                args["creator"] = get_conn
                # Cloud SQL needs explicit command_timeout and multi_statements
                args["connect_args"]["command_timeout"] = 60
                args["connect_args"]["multi_statements"] = True
                url = "mysql+pymysql://"
            else:
                # Standard local connection via URL
                # SQLAlchemy parses this URL and loads the driver internally
                args["connect_args"]["connect_timeout"] = 60

                password_part = f":{self.password}" if self.password else ""
                url = f"mysql+pymysql://{self.username}{password_part}@{self.db_path}/{self.db_name}"

                password_part = f":{self.password}" if self.password else ""
                url = f"mysql+pymysql://{self.username}{password_part}@{self.db_path}/{self.db_name}"

            if "is_tmp_db" in db_config:
                args["pool_size"] = 1
                args["pool_recycle"] = 300
            else:
                args["pool_size"] = 50
                args["pool_recycle"] = 300
            return url, args

        db_url, engine_args = get_engine_config()
        self.engine = sqlalchemy.create_engine(db_url, **engine_args)

    def close_connections(self):
        try:
            self.engine.dispose()
            if self.connector:
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
            self.engine.url,
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
                if "57P03" in error:
                    raise ResourceExhaustedError("DB Exhausted") from e
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
                "Resource Exhausted on MySQL DB. Giving up execution. Try reducing execs_per_minute."
            )
            return None, None, None

    def get_metadata(self) -> dict:
        db_metadata = {}

        try:
            with self.engine.connect() as connection:
                metadata = MetaData()
                metadata.reflect(bind=connection, schema=self.db_name)
                for table in metadata.tables.values():
                    columns = []
                    for column in table.columns:
                        columns.append(
                            {"name": column.name, "type": str(column.type)})
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
            create_statements.append(
                f"CREATE TABLE `{table.name}` ({columns});")
        return create_statements

    def create_tmp_database(self, database_name: str):
        _, _, error = self.execute(f"CREATE DATABASE {database_name};")
        if error:
            raise RuntimeError(f"Could not create database: {error}")
        self.tmp_dbs.append(database_name)

    def drop_tmp_database(self, database_name: str):
        if database_name in self.tmp_dbs:
            self.tmp_dbs.remove(database_name)
        _, _, error = self.execute(f"DROP DATABASE {database_name};")
        if error:
            logging.info(f"Could not delete database: {error}")

    def drop_all_tables(self):
        self.batch_execute(
            DROP_ALL_TABLES_QUERY.format(DATABASE=self.db_name).split(";")
        )

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
