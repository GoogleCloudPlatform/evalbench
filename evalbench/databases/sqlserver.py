import sqlalchemy

from sqlalchemy import text
from google.cloud.sql.connector import Connector
from .db import DB
from .util import (
    get_db_secret,
    rate_limited_execute,
    with_cache_execute,
    get_cache_client,
)
from typing import Any, List, Optional, Tuple

LIST_ALL_TABLES_QUERY = """
SELECT TABLE_NAME as name
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = '{DATABASE}';
"""

DROP_TABLE_COMMAND = """
DROP TABLE {TABLE_NAME};
"""

DELETE_USER_QUERY = """
DROP USER IF EXISTS "{USERNAME}"@"%";
"""

CREATE_USERS_QUERY = """
CREATE USER IF NOT EXISTS "{DQL_USERNAME}"@"%" IDENTIFIED BY "{PASSWORD}";
GRANT USAGE ON *.* TO "{DQL_USERNAME}"@"%";
GRANT SELECT ON `{DATABASE}`.* TO "{DQL_USERNAME}"@"%";
CREATE USER IF NOT EXISTS "{DML_USERNAME}"@"%" IDENTIFIED BY "{PASSWORD}";
GRANT USAGE ON *.* TO "{DML_USERNAME}"@"%";
GRANT SELECT, INSERT, UPDATE, DELETE ON `{DATABASE}`.* TO "{DML_USERNAME}"@"%";
FLUSH PRIVILEGES;
"""

class SQLServerDB(DB):

    def __init__(self, db_config):
        super().__init__(db_config)
        instance_connection_name = f"{db_config['project_id']}:{db_config['region']}:{db_config['instance_name']}"
        db_user = db_config["user_name"]
        db_pass_secret_path = db_config["password"]
        db_pass = get_db_secret(db_pass_secret_path)
        self.db_config = db_config
        self.db_name = db_config["database_name"]
        self.execs_per_minute = db_config["max_executions_per_minute"]
        self.semaphore = Semaphore(self.execs_per_minute)
        self.max_attempts = 3
        logging.getLogger("pytds").setLevel(logging.ERROR)

        # Initialize the Cloud SQL Connector object
        self.connector = Connector()

        def getconn():
            conn = self.connector.connect(
                instance_connection_name,
                "pytds",
                user=db_user,
                password=db_pass,
                db=self.db_name,
            )
            return conn

        self.engine = sqlalchemy.create_engine(
            "mssql+pytds://",
            creator=getconn,
            pool_size=50,
            pool_recycle=3600,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 60,
            },
            echo=False,
            logging_name=None,
        )

        self.cache_client = get_cache_client(db_config)

    def __del__(self):
        self.close_connections()

    def close_connections(self):
        try:
            self.engine.dispose()
            self.connector.close()
        except Exception:
            logging.warning(
                f"Failed to close connections. This may result in idle unused connections."
            )

    def generate_schema(self):
        # To be implemented
        pass

    def generate_ddl(self):
        # To be implemented
        pass

    def get_metadata(self) -> dict:
        # To be implemented
        pass

    def _execute(self, query: str) -> Tuple[Any, Any]:
        result = []
        error = None
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    resultset = connection.execute(text(query))
                    rows = resultset.fetchall()
                    for r in rows:
                        result.append(r._asdict())
        except Exception as e:
            error = str(e)
        return result, error

    def _execute_with_no_caching(self, query: str) -> Tuple[Any, Any]:
        if isinstance(self.execs_per_minute, int):
            return rate_limited_execute(
                query,
                self._execute,
                self.execs_per_minute,
                self.semaphore,
                self.max_attempts,
            )
        else:
            return self._execute(query)

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
                    raise DBResourceExhaustedError("DB Exhausted") from e
            return result, eval_result, error

        return rate_limited_execute(
            (query, eval_query, rollback),
            _run_execute,
            self.execs_per_minute,
            self.semaphore,
            self.max_attempts,
        )

    def get_metadata(self) -> dict:
        db_metadata = {}

        with self.engine.connect() as connection:
            metadata = MetaData()
            metadata.reflect(bind=connection, schema=self.db_name)
            for table in metadata.tables.values():
                columns = []
                for column in table.columns:
                    columns.append({"name": column.name, "type": str(column.type)})
                db_metadata[table.name] = columns

        return db_metadata

    #####################################################
    #####################################################
    # Setup / Teardown of temporary databases
    #####################################################
    #####################################################

    def generate_ddl(
        self,
        schema: schema_details_pb2.SchemaDetails,
    ) -> list[str]:
        create_statements = []
        for table in schema.tables:
            table_name = table.table
            columns = [
                f"{column.column} {column.data_type}" for column in table.columns
            ]
            create_statements.append(
                f"CREATE TABLE {table_name} ({",\n".join(columns)});"
            )
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
        results, _, error = self.execute(
            LIST_ALL_TABLES_QUERY.format(DATABASE=self.db_name)
        )
        if error:
            raise RuntimeError(error)
        drop_all_tables_commands = []
        for table in results:
            drop_all_tables_commands.append(
                DROP_TABLE_COMMAND.format(TABLE_NAME=table["name"])
            )
        self.batch_execute(drop_all_tables_commands)
        
    def insert_data(self, data: dict[str, List[str]]):
        if not data:
            return
        insertion_statements = []
        for table in data:
            for row in data[table]:
                insertion_statements.append(
                    f"INSERT INTO `{table}` VALUES ({",".join([f"{value}" for value in row])});"
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
