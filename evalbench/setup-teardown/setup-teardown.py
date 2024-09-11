import sys
import importlib
import logging
import argparse

from google.protobuf import text_format
from schema_detail_pb2 import SchemaDetails

sys.path.append('..')

util_config = importlib.import_module("util.config")
databaseHandler = importlib.import_module("databaseHandler")

logging.getLogger().setLevel(logging.INFO)


def parse_textproto_file(textproto_path):
    schema_details = SchemaDetails()
    with open(textproto_path, 'r') as file:
        text_format.Merge(file.read(), schema_details)
    return schema_details


def main():
    logging.info("Running setup-teardown...")

    parser = argparse.ArgumentParser(description="Setup Teardown Script")
    parser.add_argument("--setup_config_file", type=str, required=True, help="Path to the setup configuration file")
    parser.add_argument(
        "--no_data",
        action="store_true",
        help="Optional flag to indicate no data actions. Default is False."
    )

    args = parser.parse_args()

    setup_config = util_config.load_yaml_config(args.setup_config_file)
    if not setup_config:
        logging.error("Setup configuration file not found.")
        return

    db_engine = setup_config['db']
    database_name = setup_config['database_name']

    setup_file = f"schema_details/bat/{database_name}/setup.yaml"
    setup = util_config.load_yaml_config(setup_file)
    if not setup:
        logging.error("setup.yaml file not found.")
        return

    db_handler = databaseHandler.get_db_handler(setup_config)
    result, error = db_handler.drop_all_tables()
    if error:
        print("Error while dropping tables: ", error)

    setup_commands = {
      "pre_setup": [],
      "schema_creation": [],
      "post_schema_creation": [],
      "data_insertion": [],
      "post_setup": [],
      "post_data_insertion_checks": []
    }

    if args.no_data:
        sections_to_run = ["pre_setup", "schema_creation", "post_schema_creation", "post_setup"]
    else:
        sections_to_run = ["pre_setup", "schema_creation", "post_schema_creation",
                           "data_insertion", "post_setup", "post_data_insertion_checks"]

    for section in sections_to_run:
        if section in ["pre_setup", "post_schema_creation", "post_setup", "post_data_insertion_checks"]:
            commands = setup['setup_commands'][section][db_engine]
            setup_commands[section].extend(commands)

    # Create schema creation commands
    schema = parse_textproto_file(f"schema_details/bat/{database_name}/{db_engine}.textproto")
    setup_commands['schema_creation'] = db_handler.create_schema_statements(
        schema,
        setup['setup_commands']['excluded_columns'][db_engine]
    )

    # Create data insertion commands
    if not args.no_data:
        data_directory = f"datasets/bat/{database_name}/"
        setup_commands['data_insertion'] = db_handler.create_insert_statements(data_directory)

    for section in sections_to_run:
        print(f"Executing setup commands for section: {section}")
        result, error = db_handler.execute(setup_commands[section])
        if error is not None:
            print("Error is: ", error)
    print("Setup completed successfully.")


if __name__ == "__main__":
    main()
