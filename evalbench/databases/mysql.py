import sqlalchemy

from sqlalchemy import text, MetaData
from google.cloud.sql.connector import Connector
from .db import DB
from .util import (
    get_db_secret,
    rate_limited_execute,
    DBResourceExhaustedError,
)
from typing import Any, Tuple
from threading import Semaphore


class MySQLDB(DB):

    def __init__(self, db_config):
        super().__init__(db_config)
        instance_connection_name = f"{db_config['project_id']}:{db_config['region']}:{db_config['instance_name']}"
        db_user = db_config["user_name"]
        db_pass_secret_path = db_config["password"]
        db_pass = get_db_secret(db_pass_secret_path)
        self.db_name = db_config["database_name"]
        self.execs_per_minute = db_config["max_executions_per_minute"]
        self.semaphore = Semaphore(self.execs_per_minute)
        self.max_attempts = 3
        self.db_config = db_config

        # Initialize the Cloud SQL Connector object
        connector = Connector()

        def getconn():
            conn = connector.connect(
                instance_connection_name,
                "pymysql",
                user=db_user,
                password=db_pass,
                database=self.db_name,
            )
            return conn

        self.engine = sqlalchemy.create_engine(
            "mysql+pymysql://",
            creator=getconn,
            pool_size=50,
            connect_args={
                "connect_timeout": 60,
            },
        )

    def get_metadata(self) -> dict:
        metadata = MetaData()
        metadata.reflect(bind=self.engine, schema=self.db_name)

        db_metadata = {}
        for table in metadata.tables.values():
            columns = []
            for column in table.columns:
                columns.append({
                    'name': column.name,
                    'type': str(column.type)
                })
            db_metadata[table.name] = columns

        return db_metadata

    def generate_schema(self):
        # To be implemented
        pass

    def generate_ddl(self):
        # To be implemented
        pass

    def _execute(self, query: str):
        result = []
        error = None
        try:
            queries = [q.strip() for q in query.split(';') if q.strip()]
            with self.engine.connect() as connection:
                with connection.begin():
                    for query in queries:
                        resultset = connection.execute(text(query))
                        if resultset.returns_rows:
                            column_names = resultset.keys()
                            rows = resultset.fetchall()
                            for row in rows:
                                result.append(dict(zip(column_names, row)))
        except Exception as e:
            error = str(e)
            if "57P03" in error:
                raise DBResourceExhaustedError("DB Exhausted") from e
        return result, error

    def execute_dml(self, query: str, eval_query: str = None):
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

    def execute(self, query: str) -> Tuple[Any, Any]:
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
