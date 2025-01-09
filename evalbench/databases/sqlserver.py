import sqlalchemy

from sqlalchemy import text
from google.cloud.sql.connector import Connector
from .db import DB
from .util import (
    get_db_secret,
    rate_limited_execute,
)
from typing import Any, Tuple
from threading import Semaphore
import logging


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
        connector = Connector()

        def getconn():
            conn = connector.connect(
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
            connect_args={
                "connect_timeout": 60,
            },
            echo=False,
            logging_name=None,
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
