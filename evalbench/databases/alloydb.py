from .db import DB

import sqlalchemy
from sqlalchemy import text

from google.cloud.alloydb.connector import Connector
from .util import generate_ddl, get_db_secret
from typing import Any, Tuple

SCHEMA_QUERY = """
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, column_name;
"""


class AlloyDB(DB):
    def __init__(self, db_config):
        super().__init__(db_config)
        instance_connection_name = (
            f"projects/{db_config['project_id']}"
            f"/locations/{db_config['region']}/clusters/my-alloydb-cluster"
            f"/instances/{db_config['instance_name']}"
        )
        db_user = db_config["user_name"]
        db_pass_secret_path = db_config["password"]
        db_pass = get_db_secret(db_pass_secret_path)
        self.db_name = db_config["database_name"]

        # Initialize the Cloud SQL Connector object
        connector = Connector()

        def getconn():
            conn = connector.connect(
                instance_connection_name,
                "pg8000",
                user=db_user,
                password=db_pass,
                db=self.db_name,
                ip_type="public",
            )
            return conn

        self.engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=getconn,
            pool_size=50,
            connect_args={"command_timeout": 60},
        )

    def generate_schema(self):
        with self.engine.connect() as conn:
            result = conn.execute(text(SCHEMA_QUERY))
            headers = tuple(result.keys())
            rows = result.fetchall()
            return headers, rows

    def generate_ddl(self):
        headers, rows = self.generate_schema()
        return generate_ddl(rows, self.db_name)

    def execute(self, query: str) -> Tuple[Any, float]:
        result = []
        error = None
        rows = None
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    resultset = connection.execute(text(query))
            if resultset.returns_rows:
                rows = resultset.fetchall()
                for r in rows:
                    result.append(r._asdict())
        except Exception as e:
            error = str(e)
        return rows, error
