import util.config as config
import databases
from multiprocessing import Pool

config_path = "../datasets/nld/google/bat/db_configs/db_hr/csql_postgres.yaml"
db_config = config.load_yaml_config(config_path)

# Get the list of database names to drop
db = databases.get_database(db_config)
query = "SELECT datname FROM pg_database WHERE datname LIKE 'temp%'"
result = db.execute(query)
databases_to_drop = [row['datname'] for row in result[0]]  # Extract database names

# Function to drop a single database
def drop_database(db_name):
    # Each process creates a new database connection to ensure safety
    db_config = config.load_yaml_config(config_path)  # Load config in each process
    db = databases.get_database(db_config)
    drop_query = f"DROP DATABASE IF EXISTS {db_name}"
    result, error = db.execute(drop_query, use_transaction=False)
    if error:
        print(f"Failed to drop database: {error}")
    print(f"Dropped database: {db_name}")

# Use Pool to manage multiple processes
if __name__ == "__main__":
    with Pool(processes=10) as pool:  # Adjust number of processes as needed
        # Map the drop_database function to each database name
        pool.map(drop_database, databases_to_drop)
        print("All databases dropped")
