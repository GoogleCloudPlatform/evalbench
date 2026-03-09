import sqlalchemy
from sqlalchemy import text
from google.cloud.sql.connector import Connector

def create_database(db_config, db_name):
    db_type = db_config["db_type"]
    db_path = db_config["database_path"]
    username = db_config.get("user_name") or ""
    password = db_config.get("password") or ""
    
    if db_type == "postgres":
        connector = Connector()
        def get_conn():
            return connector.connect(db_path, "pg8000", user=username, password=password, db="postgres")
        engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=get_conn, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            try:
                conn.execute(text(f"CREATE DATABASE {db_name};"))
            except Exception as e:
                if 'already exists' not in str(e):
                    print(f"Postgres CREATE DATABASE Error: {e}")
    elif db_type == "mysql":
        connector = Connector()
        def get_conn():
            return connector.connect(db_path, "pymysql", user=username, password=password, db="sys")
        engine = sqlalchemy.create_engine("mysql+pymysql://", creator=get_conn, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            try:
                conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {db_name};"))
            except Exception as e:
                print(f"MySQL CREATE DATABASE Error: {e}")
    elif db_type == "spanner":
        from google.cloud import spanner
        spanner_client = spanner.Client()
        instance_id = db_path.split("/")[-1]
        instance = spanner_client.instance(instance_id)
        database = instance.database(db_name)
        try:
            op = database.create()
            op.result() # Wait for completion
        except Exception as e:
            if 'Already exists' not in str(e):
                print(f"Spanner CREATE DATABASE Error: {e}")
    elif db_type == "sqlite":
        import sqlite3
        import os
        filename = f"{db_path}/{db_name}.db"
        os.makedirs(db_path, exist_ok=True)
        conn = sqlite3.connect(filename)
        conn.close()
    else:
        print(f"create_database not implemented for db_type={db_type}")

