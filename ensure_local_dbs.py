import psycopg2
import pymysql
import os
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Full Mapping of Source DB -> Target DB Name
DBS = {
    # BIRD
    "california_schools": "bird_california_schools",
    "superhero": "bird_superhero",
    "student_club": "bird_student_club",
    "toxicology": "bird_toxicology",
    "thrombosis_prediction": "bird_thrombosis_pred",
    "formula_1": "bird_formula_1",
    "debit_card_specializing": "bird_debit_card_spec",
    "financial": "bird_financial",
    "card_games": "bird_card_games",
    "codebase_community": "bird_codebase_community",
    "european_football_2": "bird_european_football_2",
    # BIAS
    "bias_credit": "bias_credit",
    "bias_hr": "bias_hr",
    "bias_medical": "bias_medical",
    "bias_demographics": "bias_demographics",
    # Air Travel
    "air_travel": "air_travel"
}

def create_postgres_dbs():
    print("Checking Postgres DBs...")
    user = os.environ.get("USER", "your-username")
    # Connect to default 'postgres' db
    try:
        conn = psycopg2.connect(
            database="postgres", 
            user=user, 
            host="/var/run/postgresql", 
            port="5432"
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        for _, db_name in DBS.items():
            cursor.execute(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'")
            exists = cursor.fetchone()
            if not exists:
                print(f"Creating Postgres DB: {db_name}")
                cursor.execute(f"CREATE DATABASE \"{db_name}\"")
            else:
                print(f"Postgres DB {db_name} exists.")
                
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Postgres Connection Failed: {e}")

def create_mysql_dbs():
    print("Checking MySQL DBs...")
    user = os.environ.get("USER", "your-username")
    try:
        conn = pymysql.connect(
            host="localhost",
            user=user,
            password="",
            unix_socket="/var/run/mysqld/mysqld.sock"
        )
        cursor = conn.cursor()
        
        for _, db_name in DBS.items():
            cursor.execute(f"SHOW DATABASES LIKE '{db_name}'")
            exists = cursor.fetchone()
            if not exists:
                print(f"Creating MySQL DB: {db_name}")
                cursor.execute(f"CREATE DATABASE `{db_name}`")
            else:
                print(f"MySQL DB {db_name} exists.")
                
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"MySQL Connection Failed: {e}")

if __name__ == "__main__":
    create_postgres_dbs()
    create_mysql_dbs()
