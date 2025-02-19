from multiprocessing import Value
from queue import Queue
from copy import deepcopy
from databases import DB, get_database
from util.config import load_db_data_from_csvs, load_textproto
from evalproto import (
    schema_details_pb2,
)

def build_db_queue(core_db: DB, db_config, setup_config, query_type: str, num_dbs: int):
    if query_type == "dql":
        return _prepare_db_queue_for_dql(core_db, db_config, setup_config, num_dbs)
    elif query_type == "dml":
        return _prepare_db_queue_for_dml(core_db, db_config, setup_config, num_dbs)
    elif query_type == "ddl":
        return _prepare_db_queue_for_ddl(core_db, db_config, setup_config, num_dbs)
    return Queue[DB]()

def _prepare_db_queue_for_dql(core_db: DB, db_config, setup_config, num_dbs):
    """For DQL, use the same single DB with a user that has only DQL access."""
    db_queue = Queue[DB]()
    dql_db_config = deepcopy(db_config)
    if setup_config:
        data, schema = _get_setup_values(setup_config)
        core_db.set_setup_instructions(setup_config, data, schema)
        core_db.resetup_database(False, True)
        dql_db_config["user_name"] = core_db.get_dql_user()
        dql_db_config["password"] = core_db.get_tmp_user_password()
    singular_db = get_database(dql_db_config)
    for _ in range(num_dbs):
        db_queue.put(singular_db)
    return db_queue

def _prepare_db_queue_for_dml(core_db: DB, db_config, setup_config, num_dbs):
    """For DML, use the same single DB with a user that has only DQL / DML access."""
    db_queue = Queue[DB]()
    dml_db_config = deepcopy(db_config)
    if setup_config:
        data, schema = _get_setup_values(setup_config)
        core_db.set_setup_instructions(setup_config, data, schema)
        core_db.resetup_database(False, True)
        dml_db_config["user_name"] = core_db.get_dml_user()
        dml_db_config["password"] = core_db.get_tmp_user_password()
    singular_db = get_database(dml_db_config)
    for _ in range(num_dbs):
        db_queue.put(singular_db)
    return db_queue

def _prepare_db_queue_for_ddl(core_db: DB, db_config, setup_config, num_dbs):
    """For DQL, use the same single DB with a user that has only DQL access."""
    db_queue = Queue[DB]()
    if not setup_config:
        raise ValueError("No Setup Config was provided for DDL")
    _, schema = _get_setup_values(setup_config)
    tmp_dbs = core_db.create_tmp_databases(db_config, num_dbs)
    for db_name in tmp_dbs:
        tmp_ddl_db_config = deepcopy(db_config)
        tmp_ddl_db_config["database_name"] = db_name
        tmp_ddl_db_config["is_tmp_db"] = True
        tmp_db = get_database(tmp_ddl_db_config)
        tmp_db.set_setup_instructions(setup_config, None, schema)
        db_queue.put(tmp_db)
    return db_queue

def _get_setup_values(setup_config):
    data = load_db_data_from_csvs(setup_config["data_directory"])
    schema = load_textproto(setup_config["schema_path"], schema_details_pb2.SchemaDetails())
    return data, schema