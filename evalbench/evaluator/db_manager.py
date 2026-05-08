from queue import Queue
from copy import deepcopy
from databases import DB, get_database
from util.config import load_db_data_from_csvs, load_setup_scripts
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Optional
import logging


def build_db_queue(
    core_db: DB, db_name, db_config, setup_config, query_type: str, num_dbs: int
):
    logging.info(f"Building DB queue (query_type='{query_type}') with {num_dbs} pools for {db_name}...")
    if query_type == "dql":
        return _prepare_db_queue_for_dql(
            core_db, db_name, db_config, setup_config, num_dbs
        )
    elif query_type == "dml":
        return _prepare_db_queue_for_dml(
            core_db, db_name, db_config, setup_config, num_dbs
        )
    elif query_type == "ddl":
        return _prepare_db_queue_for_ddl(
            core_db, db_name, db_config, setup_config, num_dbs
        )

    logging.info(f"Finished building DB queue for query_type '{query_type}' on {db_name}")
    return Queue[DB]()


def _prepare_db_queue_for_dql(core_db: DB, db_name, db_config, setup_config, num_dbs):
    """For DQL, use the same single DB with a user that has only DQL access."""
    db_queue = Queue[DB]()
    dql_db_config = deepcopy(db_config)
    if setup_config:
        setup_scripts, data = _get_setup_values(
            setup_config, db_name, db_config.get("db_type")
        )
        core_db.set_setup_instructions(setup_scripts, data)
        core_db.resetup_database(False, True)
        dql_db_config["user_name"] = core_db.get_dql_user()
        dql_db_config["password"] = core_db.get_tmp_user_password()
    singular_db = get_database(dql_db_config, db_name)
    for _ in range(num_dbs):
        db_queue.put(singular_db)
    return db_queue


def _prepare_db_queue_for_dml(core_db: DB, db_name, db_config, setup_config, num_dbs):
    """For DML, use the same single DB with a user that has only DQL / DML access."""
    db_queue = Queue[DB]()
    dml_db_config = deepcopy(db_config)
    if setup_config:
        setup_scripts, data = _get_setup_values(
            setup_config, db_name, db_config.get("db_type")
        )
        core_db.set_setup_instructions(setup_scripts, data)
        core_db.resetup_database(False, True)
        dml_db_config["user_name"] = core_db.get_dml_user()
        dml_db_config["password"] = core_db.get_tmp_user_password()
    singular_db = get_database(dml_db_config, db_name)
    for _ in range(num_dbs):
        db_queue.put(singular_db)
    return db_queue


def _prepare_db_queue_for_ddl(core_db: DB, db_name, db_config, setup_config, num_dbs):
    """For DDL, use the same single DB with a user that has only DDL access."""
    if setup_config:
        setup_scripts, _ = _get_setup_values(
            setup_config, db_name, db_config.get("db_type")
        )
    core_db.set_setup_instructions(setup_scripts, None)
    core_db.resetup_database(False, False)
    db_queue = Queue[DB]()
    if not setup_config:
        raise ValueError("No Setup Config was provided for DDL")
    setup_scripts, _ = _get_setup_values(
        setup_config, db_name, db_config.get("db_type")
    )
    tmp_dbs = core_db.create_tmp_databases(num_dbs)
    with ThreadPoolExecutor() as executor:
        create_ddl_tmp_db_p = partial(
            _create_ddl_tmp_db, db_config=db_config, setup_scripts=setup_scripts
        )
        results = executor.map(create_ddl_tmp_db_p, tmp_dbs)
        for tmp_db in results:
            db_queue.put(tmp_db)
    return db_queue


def _create_ddl_tmp_db(tmp_db, db_config, setup_scripts):
    tmp_ddl_db_config = deepcopy(db_config)
    tmp_ddl_db_config["is_tmp_db"] = True
    tmp_db = get_database(tmp_ddl_db_config, tmp_db)
    tmp_db.set_setup_instructions(setup_scripts, None)
    return tmp_db


def _get_setup_values(setup_config, db_name: str, db_type: str):
    try:
        scripts_path = setup_config["setup_directory"] + "/" + db_name + "/" + db_type
        data_path = setup_config["setup_directory"] + "/" + db_name + "/data"

        logging.info(f"Loading DB setup files from location: {scripts_path}")
        setup_scripts = load_setup_scripts(scripts_path)

        logging.info(f"Loading data populate files from location: {data_path}")
        data = load_db_data_from_csvs(data_path)

        return setup_scripts, data
    except Exception as e:
        raise FileNotFoundError(
            f"Could not find setup files for database {db_name} on {db_type} due to: {e}"
        )
