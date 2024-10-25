import sqlalchemy
from abc import ABC, abstractmethod
from typing import Any, Tuple
from sqlalchemy import text, MetaData
from google.cloud.sql.connector import Connector
from typing import Any, Tuple
from threading import Semaphore
import logging

class MySQLDB():
    def __init__(self):
        instance_connection_name = f"cloud-db-nl2sql:us-central1:nl2sql-birdsql-mysql"
        db_user = "birdsql"
        db_pass = "Gener@teSQL4Me"
        self.db_name = "california_schools"

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

    def execute(self, query: str):
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
        return result, error

db_obj = MySQLDB()
query = "SELECT MAX(`frpm`.`Percent (%) Eligible Free (K-12)`) FROM `frpm` WHERE `frpm`.`County Name` = 'Alameda';"
result, error = db_obj.execute(query)
if error:
    print(error)
else:
    print(result)