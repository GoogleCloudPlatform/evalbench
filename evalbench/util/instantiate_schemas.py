import argparse
import sys
import os

# Ensure evalbench is in sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from evalbench.util.config import load_yaml_config, load_textproto
from evalbench.dataset.dataset import load_dataset_from_json, flatten_dataset
from evalbench.databases import get_database
from evalbench.evaluator.db_manager import _get_setup_values
from evalbench.evalproto import schema_pb2
from evalbench.databases.util import DatabaseSchema, Table, Column, View

def proto_to_dataclass(proto_schema: schema_pb2.DatabaseSchema) -> DatabaseSchema:
    schema = DatabaseSchema(name=proto_schema.name)
    for t in proto_schema.tables:
        table = Table(name=t.name)
        for c in t.columns:
            table.columns.append(Column(name=c.name, type=c.type, is_primary_key=c.is_primary_key))
        schema.tables.append(table)
    for v in proto_schema.views:
        view = View(name=v.name)
        for c in v.columns:
            view.columns.append(Column(name=c.name, type=c.type, is_primary_key=c.is_primary_key))
        schema.views.append(view)
    return schema

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
            # 1. Ensure the database exists
            from evalbench.databases.creator import create_database
            create_database(db_config, db_name)
            
            # 2. Get connection to the specific database
            core_db = get_database(db_config, db_name)
            
            # 3. Load setup scripts or textproto
            setup_config = config
            try:
                setup_scripts, data = _get_setup_values(setup_config, db_name, db_type)
            except Exception as e:
                print(f"  Failed to load setup values: {e}")
                continue
            
            # Check for textproto overlay
            schema_path = os.path.join(setup_config["setup_directory"], db_name, "schema.textproto")
            if os.path.exists(schema_path):
                print(f"  Found textproto schema at {schema_path}")
                proto = schema_pb2.DatabaseSchema()
                load_textproto(schema_path, proto)
                dataclass_schema = proto_to_dataclass(proto)
                # Ensure the textproto overrides or replaces setup_scripts[1] (which is setup.sql)
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
