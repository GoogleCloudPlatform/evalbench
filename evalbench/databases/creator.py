import sqlalchemy
from sqlalchemy import text
from google.cloud.sql.connector import Connector
from google.api_core import exceptions

def create_database(db_config, db_name):
    db_type = db_config["db_type"]
    db_path = db_config["database_path"]
    username = db_config.get("user_name") or ""
    password = db_config.get("password") or ""
    
    if db_type == "postgres":
        connector = Connector()
        try:
            def get_conn():
                return connector.connect(db_path, "pg8000", user=username, password=password, db="postgres")
            engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=get_conn, isolation_level="AUTOCOMMIT")
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"CREATE DATABASE {db_name};"))
                except sqlalchemy.exc.ProgrammingError as e:
                    if 'already exists' not in str(e):
                        raise RuntimeError(f"Failed to create Postgres DB {db_name}: {e}") from e
                except Exception as e:
                    raise RuntimeError(f"Failed to connect and create Postgres DB {db_name}: {e}") from e
        finally:
            connector.close()
            
    elif db_type == "mysql":
        connector = Connector()
        try:
            def get_conn():
                return connector.connect(db_path, "pymysql", user=username, password=password, db="sys")
            engine = sqlalchemy.create_engine("mysql+pymysql://", creator=get_conn, isolation_level="AUTOCOMMIT")
            with engine.connect() as conn:
                try:
                    conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {db_name};"))
                except Exception as e:
                    raise RuntimeError(f"Failed to create MySQL DB {db_name}: {e}") from e
        finally:
            connector.close()
            
    elif db_type == "spanner":
        from google.cloud import spanner
        with spanner.Client() as spanner_client:
            instance_id = db_path.split("/")[-1]
            instance = spanner_client.instance(instance_id)
            database = instance.database(db_name)
            try:
                op = database.create()
                op.result() # Wait for completion
            except exceptions.AlreadyExists:
                pass
            except Exception as e:
                raise RuntimeError(f"Failed to create Spanner DB {db_name}: {e}") from e
                
    elif db_type == "sqlite":
        import sqlite3
        import os
        filename = f"{db_path}/{db_name}.db"
        os.makedirs(db_path, exist_ok=True)
        conn = sqlite3.connect(filename)
        conn.close()
    else:
        print(f"create_database not implemented for db_type={db_type}")
