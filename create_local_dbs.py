import sqlalchemy
from sqlalchemy import text
import yaml

DBS = [
"california_schools",
"card_games",
"codebase_community",
"debit_card_specializing",
"european_football_2",
"financial",
"formula_1",
"student_club",
"superhero",
"thrombosis_prediction",
"toxicology"
]

def create_dbs(config_path, engine_type):
    print(f"Creating {engine_type} databases...")
    with open(config_path, 'r') as f:
        conf = yaml.safe_load(f)
    
    # Connect to default DB (e.g. postgres or mysql or sys)
    if "postgres" in engine_type:
        url = f"postgresql+pg8000://{conf['user_name']}:{conf.get('password') or ''}@/{conf['database_name']}"
        # pg8000 socket hack?
        import os
        socket_path = "/var/run/postgresql/.s.PGSQL.5432"
        connect_args = {}
        if os.path.exists(socket_path):
            connect_args["unix_sock"] = socket_path
            url = f"postgresql+pg8000://{conf['user_name']}:{conf.get('password') or ''}@/"
        
        # Connect to 'postgres' database to issue CREATE DATABASE
        url += "postgres"
    elif "mysql" in engine_type:
        url = f"mysql+pymysql://{conf['user_name']}:{conf.get('password') or ''}@{conf['database_path']}/mysql"
        connect_args = {}
    
    engine = sqlalchemy.create_engine(url, connect_args=connect_args, isolation_level="AUTOCOMMIT")
    
    with engine.connect() as conn:
        for db in DBS:
            target_name = f"bird_{db}"
            print(f"Creating {target_name}...")
            try:
                conn.execute(text(f"CREATE DATABASE {target_name}"))
            except Exception as e:
                print(f"Error (maybe exists): {e}")

if __name__ == "__main__":
    create_dbs("datasets/db_configs/postgres.yaml", "postgres")
    create_dbs("datasets/db_configs/mysql.yaml", "mysql")
