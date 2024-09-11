import sqlalchemy
from sqlalchemy import text
from google.cloud.sql.connector import Connector
from .db import DB
from .util import generate_ddl
from typing import Any, Tuple


class MySQLDB(DB):

    def __init__(self, db_config):
        super().__init__(db_config)
        instance_connection_name = f"{db_config['project_id']}:{db_config['region']}:{db_config['instance_name']}"
        db_user = db_config["user_name"]
        db_pass = db_config["password"]
        self.db_name = db_config["database_name"]

        # Initialize the Cloud SQL Connector object
        connector = Connector()

        def getconn():
            conn = connector.connect(
                instance_connection_name,
                'pymysql',
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

    def generate_schema(self):
        # To be implemented
        pass

    def generate_ddl(self):
        # To be implemented
        pass

    def execute(self, query: str) -> Tuple[Any, float]:
        result = []
        error = None
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    resultset = connection.execute(text(query))
            if resultset.returns_rows:
                column_names = resultset.keys()
                rows = resultset.fetchall()
                for row in rows:
                    result.append(dict(zip(column_names, row)))
        except Exception as e:
            error = str(e)
        return result, error
