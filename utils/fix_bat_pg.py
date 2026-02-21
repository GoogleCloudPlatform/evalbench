import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "evalbench"))

def fix_bat_pg():
    print("Fixing BAT (db_blog) on Postgres...")
    
    # 1. Create DB if missing
    user = os.environ.get("USER", "your-username")
    try:
        conn = psycopg2.connect(database="postgres", user=user, host="/var/run/postgresql", port="5432")
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = 'db_blog'")
        if not cur.fetchone():
            print("Creating db_blog...")
            cur.execute("CREATE DATABASE db_blog")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error creating DB: {e}")
        return

    # 2. Run Setup SQL
    setup_file = "datasets/bat/setup/db_blog/postgres/setup.sql"
    if not os.path.exists(setup_file):
        print(f"Setup file not found: {setup_file}")
        return

    print(f"Applying {setup_file}...")
    try:
        conn = psycopg2.connect(database="db_blog", user=user, host="/var/run/postgresql", port="5432")
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        with open(setup_file, 'r') as f:
            sql = f.read()
            cur.execute(sql)
            
        cur.close()
        conn.close()
        print("BAT (PG) Setup Complete.")
    except Exception as e:
        print(f"Error running setup SQL: {e}")

if __name__ == "__main__":
    fix_bat_pg()
