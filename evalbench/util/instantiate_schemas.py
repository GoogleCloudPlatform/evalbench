import argparse
import sys
import os
import re

# Ensure evalbench is in sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from evalbench.util.config import load_yaml_config
from evalbench.dataset.dataset import load_dataset_from_json, flatten_dataset
from evalbench.databases import get_database
from evalbench.evaluator.db_manager import _get_setup_values
from evalbench.databases.util import DatabaseSchema, Table, Column

def parse_textproto_to_dataclass(filepath: str, db_name: str) -> DatabaseSchema:
    schema = DatabaseSchema(name=db_name)
    with open(filepath, "r") as f:
        content = f.read()

    # Simple state machine to parse textproto flat structure
    current_table = None
    in_columns = False
    current_col_name = None
    current_col_type = None

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("tables: {"):
            if current_table:
                schema.tables.append(current_table)
            current_table = Table(name="")
        elif line.startswith('table: "') and current_table:
            current_table.name = line.split('"')[1]
        elif line.startswith("columns: {"):
            in_columns = True
            current_col_name = None
            current_col_type = None
        elif in_columns and line.startswith('column: "') and current_table:
            current_col_name = line.split('"')[1]
        elif in_columns and line.startswith('data_type: "') and current_table:
            current_col_type = line.split('"')[1]
        elif in_columns and line.startswith("}"):
            if current_table and current_col_name and current_col_type:
                is_pk = "NOT NULL" in current_col_type.upper() # basic heuristic
                # Clean up type if needed
                cleaned_type = current_col_type.replace("NOT NULL", "").strip()
                current_table.columns.append(Column(name=current_col_name, type=cleaned_type, is_nullable=not is_pk))
            in_columns = False

    if current_table and current_table.name:
        schema.tables.append(current_table)
        
    return schema if schema.tables else None

def _load_schema_from_directory(setup_directory: str, db_name: str, db_type: str) -> DatabaseSchema:
    # Look for schemas/{db_name}/{db_type}.textproto parallel to setup_directory
    dataset_dir = os.path.dirname(setup_directory.rstrip('/'))
    schema_path = os.path.join(dataset_dir, "schemas", db_name, f"{db_type}.textproto")
    if not os.path.exists(schema_path):
        # Fallback
        schema_path = os.path.join(setup_directory, db_name, "schema.textproto")
    if os.path.exists(schema_path):
        return parse_textproto_to_dataclass(schema_path, db_name)
    return None

def instantiate_schemas(config_path: str):
    config = load_yaml_config(config_path)
    
    # Load dataset to figure out what DBs are needed
    dataset = load_dataset_from_json(config["dataset_config"], config)
    dataset = flatten_dataset(dataset)
    
    # Get unique databases (db_ids) needed for this experiment
    unique_db_names = set(item.database for item in dataset)
    
    print(f"Instantiating schemas for: {unique_db_names}")
    
    for db_config_path in config.get("database_configs", []):
        db_config = load_yaml_config(db_config_path)
        db_type = db_config["db_type"]
        
        for db_name in unique_db_names:
            print(f"Processing {db_name} for engine {db_type}...")
            
            # Get connection wrapper to the specific database
            core_db = get_database(db_config, db_name)
            
            # Ensure the permanent database exists BEFORE running resetup_database
            core_db.ensure_database_exists(db_name)
            
            # Load setup scripts or textproto
            setup_config = config
            try:
                setup_scripts, data = _get_setup_values(setup_config, db_name, db_type)
            except Exception as e:
                print(f"  Failed to load setup values: {e}")
                continue
            
            # Check for textproto overlay
            dataclass_schema = _load_schema_from_directory(setup_config["setup_directory"], db_name, db_type)
            if dataclass_schema:
                print(f"  Found textproto schema for {db_name}")
                ddl_statements = core_db.generate_ddl(dataclass_schema)
                setup_scripts = (setup_scripts[0], ddl_statements, setup_scripts[2])
            
            core_db.set_setup_instructions(setup_scripts, data)
            
            # Execute setup!
            try:
                core_db.resetup_database(force=True, setup_users=False)
                print(f"  Successfully instantiated {db_name} on {db_type}")
            except Exception as e:
                print(f"  Failed to setup {db_name} on {db_type}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment_config", type=str, required=True)
    args = parser.parse_args()
    instantiate_schemas(args.experiment_config)
