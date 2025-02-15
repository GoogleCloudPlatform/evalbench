import logging
from .database_handler import get_db_handler
from databases import DB
from evalproto.schema_details_pb2 import SchemaDetails

logging.getLogger().setLevel(logging.INFO)


def create_temp_databases(db_config: dict, db: DB, num_database: int):
    db_handler = get_db_handler(db, db_config["database_name"])
    return db_handler.create_temp_databases(num_database)


def drop_temp_databases(db_config: dict, db: DB, temp_databases: list):
    db_handler = get_db_handler(db, db_config["database_name"])
    return db_handler.drop_temp_databases(temp_databases)


def setup_database(
    database: DB,
    setup_config: dict,
    fill_data: bool = False,
):
    def run_setup():
        logging.info("Running setup-teardown...")

        db_handler = get_db_handler(database, "")
        schema = setup_config["schema"]
        if fill_data:
            db_data = setup_config["db_data"]
        else:
            db_data = {}

        logging.info(setup_config)
        logging.info("\n\n\n*********************************\n\n\n")
        logging.info(db_handler.create_schema_statements(
            schema, setup_config["setup_commands"]["excluded_columns"]["postgres"])
        )
        return True

        result, error = db_handler.drop_all_tables()
        if error:
            logging.error(f"Error while dropping tables: {error}")
            return False

        setup_commands = {
            "pre_setup": [],
            "schema_creation": [],
            "post_schema_creation": [],
            "data_insertion": [],
            "post_setup": [],
            "post_data_insertion_checks": [],
        }

        for section in ["pre_setup", "post_schema_creation", "post_setup"]:
            commands = setup["setup_commands"][section][db_engine]
            setup_commands[section].extend(commands)

        setup_commands["schema_creation"] = db_handler.create_schema_statements(
            schema, setup["setup_commands"]["excluded_columns"][db_engine]
        )

        if not no_data:
            data_directory = experiment_config["data_directory"]
            setup_commands["data_insertion"] = db_handler.create_insert_statements(
                data_directory
            )
            setup_commands["post_data_insertion_checks"] = setup["setup_commands"][
                "post_data_insertion_checks"
            ][db_engine]
            if setup_commands["post_data_insertion_checks"]:
                combined_query = " UNION ALL ".join(
                    setup_commands["post_data_insertion_checks"]
                )
                setup_commands["post_data_insertion_checks"] = [combined_query]

        logging.info("Setup completed successfully.")
        return True

    # Attempt the setup process and retry if it fails
    max_retries = 1
    for attempt in range(max_retries + 1):
        success = run_setup()
        if success:
            break
        if attempt < max_retries:
            logging.info(f"Retrying setup process... (Attempt {attempt + 2})")
        else:
            raise Exception("Setup process failed after retrying.")
