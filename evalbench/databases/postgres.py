from sqlalchemy.pool import NullPool
import sqlalchemy
from sqlalchemy import text, MetaData
import logging

from .db import DB
from google.cloud.sql.connector import Connector
from .util import (
    generate_ddl,
    get_db_secret,
    rate_limited_execute,
    with_cache_execute,
    get_cache_client,
    DBResourceExhaustedError,
)
from util.config import generate_key
from typing import Any, Tuple
from threading import Semaphore

SCHEMA_QUERY = """
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, column_name;
"""

DROP_ALL_TABLES_QUERY = """
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
"""

DELETE_USER_QUERY = """
REVOKE USAGE ON SCHEMA public FROM {USERNAME};
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {USERNAME};
DROP USER IF EXISTS {USERNAME};
"""

CREATE_USER_QUERY = """
CREATE USER {DQL_USERNAME} WITH PASSWORD '{PASSWORD}';
GRANT USAGE ON SCHEMA public TO {DQL_USERNAME};
GRANT SELECT ON ALL TABLES IN SCHEMA public TO {DQL_USERNAME};

CREATE USER {DML_USERNAME} WITH PASSWORD '{PASSWORD}';
GRANT USAGE ON SCHEMA public TO {DML_USERNAME};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {DML_USERNAME};
"""


class PGDB(DB):

    def __init__(self, db_config):
        super().__init__(db_config)
        instance_connection_name = f"{db_config['project_id']}:{db_config['region']}:{db_config['instance_name']}"
        db_user = db_config["user_name"]
        db_pass = get_db_secret(db_config["password"])
        self.db_name = db_config["database_name"]
        self.execs_per_minute = db_config["max_executions_per_minute"]
        self.is_tmp_db = "is_tmp_db" in db_config
        self.db_config = db_config
        self.semaphore = Semaphore(self.execs_per_minute)
        self.max_attempts = 3
        self.tmp_dbs = []
        self.tmp_users = []
        self.was_re_setup_this_session = False

        # Initialize the Cloud SQL Connector object
        self.connector = Connector()

        def get_conn():
            conn = self.connector.connect(
                instance_connection_name,
                "pg8000",
                user=db_user,
                password=db_pass,
                db=self.db_name,
            )
            return conn

        def get_engine_args(is_tmp_db):
            common_args = {
                "creator": get_conn,
                "connect_args": {"command_timeout": 60},
            }
            if is_tmp_db:
                common_args["poolclass"] = NullPool
            else:
                common_args["pool_size"] = 50
                common_args["pool_recycle"] = 300
            return common_args

        self.engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            **get_engine_args(self.is_tmp_db)
        )

        self.cache_client = get_cache_client(db_config)

    def clean_tmp_creations(self):
        self.drop_tmp_databases(self.tmp_dbs.copy())
        self.delete_tmp_users(self.tmp_users.copy())

    def close_connections(self):
        try:
            self.engine.dispose()
            self.connector.close()
        except Exception:
            logging.warning(
                f"Failed to close connections. This may result in idle unused connections."
            )

    def execute(self, query: str, use_cache=False) -> Tuple[Any, Any]:
        """
        Execute a query with optional caching. Falls back to the original logic if caching is not provided.

        Args:
            query (str): The SQL query to execute.
            cache_client: An optional caching client (e.g., Redis).

        Returns:
            Tuple[Any, Any]: The query results and any error message (None if successful).
        """
        if not use_cache or not self.cache_client:
            return self._execute_with_no_caching(query)

        return with_cache_execute(
            query,
            self.engine.url,
            self._execute_with_no_caching,
            self.cache_client,
        )

    def execute_dml(self, query: str, eval_query: str | None = None):
        return rate_limited_execute(
            (query, eval_query),
            self._execute_dml,
            self.execs_per_minute,
            self.semaphore,
            self.max_attempts,
        )

    def _execute_with_no_caching(self, query: str) -> Tuple[Any, Any]:
        return rate_limited_execute(
            (query,),
            self._execute,
            self.execs_per_minute,
            self.semaphore,
            self.max_attempts,
        )

    def _execute(self, query: str):
        result = []
        error = None
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    resultset = connection.execute(text(query))
                    if resultset.returns_rows:
                        rows = resultset.fetchall()
                        result.extend(r._asdict() for r in rows)
        except Exception as e:
            error = str(e)
            if "57P03" in error:
                raise DBResourceExhaustedError("DB Exhausted") from e
        return result, error

    def _execute_auto_commit(self, query: str):
        error = None
        try:
            with self.engine.connect() as connection:
                connection.execution_options(isolation_level="AUTOCOMMIT").execute(
                    text(query)
                )
        except Exception as e:
            error = str(e)
        return error == None, error

    def _execute_dml(self, query: str, eval_query: str | None = None):
        result = []
        eval_result = []
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

                    transaction.rollback()
        except Exception as e:
            error = str(e)
        return result, eval_result, error

    def set_setup_instructions(self, setup_config, data, schema):
        self.setup_config = setup_config
        self.data = data
        self.schema = schema

    def resetup_database(self, force=False, setup_users=False):
        if not self.setup_config:
            raise ValueError("Setup config is required for setup.")
        if self.was_re_setup_this_session and not force:
            # If database was already re-setup (for DQL, DML, etc.)
            # and it can be re-used, don't re-run setup unless forced.
            return
        self.drop_all_tables()
        self.run_setup_commands("pre_setup")
        self.setup_schema()
        self.run_setup_commands("post_schema_creation")
        self.insert_data()
        self.run_setup_commands("post_setup")
        if setup_users:
            self.setup_tmp_users()
        self.was_re_setup_this_session = True
        return

    def run_setup_commands(self, section):
        if section in self.setup_config and "postgres" in self.setup_config[section]:
            _, error = self.execute(";\n".join(self.setup_config[section]["postgres"]))
            if error:
                raise RuntimeError(
                    f"Could not run setup instructions for {section}: {error}"
                )

    def create_tmp_databases(self, db_config, num_dbs: int):
        tmp_dbs = []
        for _ in range(num_dbs):
            tmp_db_name = f"tmp_{db_config["database_name"]}_{generate_key()}"
            self.create_tmp_database(tmp_db_name)
            tmp_dbs.append(tmp_db_name)
        return tmp_dbs

    def create_tmp_database(self, database_name: str):
        _, error = self._execute_auto_commit(f"CREATE DATABASE {database_name};")
        if error:
            raise RuntimeError(f"Could not create database: {error}")
        self.tmp_dbs.append(database_name)

    def drop_tmp_databases(self, databases):
        for database_name in databases:
            self.drop_tmp_database(database_name)

    def drop_tmp_database(self, database_name: str):
        if database_name in self.tmp_dbs:
            self.tmp_dbs.remove(database_name)
        _, error = self._execute_auto_commit(f"DROP DATABASE {database_name};")
        if error:
            logging.info(f"Could not delete database: {error}")

    def drop_all_tables(self):
        _, error = self.execute(DROP_ALL_TABLES_QUERY)
        if error:
            raise RuntimeError(error)

    def get_metadata(self) -> dict:
        db_metadata = {}

        with self.engine.connect() as connection:
            metadata = MetaData()
            metadata.reflect(bind=connection, schema="public")
            for table in metadata.tables.values():
                columns = []
                for column in table.columns:
                    columns.append({"name": column.name, "type": str(column.type)})
                db_metadata[table.name] = columns

        return db_metadata

    def setup_schema(self):
        if not self.schema:
            return
        excluded_columns = set()
        if (
            "excluded_columns" in self.setup_config
            and "postgres" in self.setup_config["excluded_columns"]
        ):
            excluded_columns = self.setup_config["excluded_columns"]["postgres"]
        create_statements = []
        for table in self.schema.tables:
            table_name = table.table
            columns = [
                f"{column.column} {column.data_type}"
                for column in table.columns
                if column.column not in excluded_columns
            ]
            create_statements.append(
                f"CREATE TABLE {table_name} ({",\n".join(columns)});"
            )
        _, error = self.execute("\n".join(create_statements))
        if error:
            raise RuntimeError(f"Could not setup database schema: {error}")

    def generate_schema(self):
        with self.engine.connect() as conn:
            result = conn.execute(text(SCHEMA_QUERY))
            headers = tuple(result.keys())
            rows = result.fetchall()
        return headers, rows

    def generate_ddl(self):
        headers, rows = self.generate_schema()
        return generate_ddl(rows, self.db_name)

    def insert_data(self):
        if not self.data:
            return
        insertion_statements = []
        for table in self.data:
            for row in self.data[table]:
                insertion_statements.append(
                    f"INSERT INTO public.{table} VALUES ({", ".join([f"{value}" for value in row])});"
                )
        _, error = self.execute("\n".join(insertion_statements))
        if error:
            raise RuntimeError(f"Could not setup database schema: {error}")

    def setup_tmp_users(self):
        self.dql_user = "tmp_dql_user_" + generate_key()
        self.dml_user = "tmp_dml_user_" + generate_key()
        self.tmp_user_password = generate_key()
        _, error = self.execute(
            CREATE_USER_QUERY.format(
                DQL_USERNAME=self.dql_user,
                DML_USERNAME=self.dml_user,
                PASSWORD=self.tmp_user_password,
            )
        )
        if error:
            raise RuntimeError(f"Could not setup users. {error}")
        self.tmp_users.extend([self.dql_user, self.dml_user])

    def delete_tmp_users(self, users):
        for username in users:
            self.delete_tmp_user(username)

    def delete_tmp_user(self, username):
        if username in self.tmp_users:
            self.tmp_users.remove(username)
        _, error = self.execute(DELETE_USER_QUERY.format(USERNAME=username))
        if error:
            logging.info(f"Could not delete tmp user due to {error}")

    def get_dql_user(self):
        if not self.dql_user:
            raise RuntimeError("No DQL user was created by this connection.")
        return self.dql_user

    def get_dml_user(self):
        if not self.dql_user:
            raise RuntimeError("No DML user was created by this connection.")
        return self.dml_user

    def get_tmp_user_password(self):
        if not self.dql_user:
            raise RuntimeError("No users were created by this connection.")
        return self.tmp_user_password
