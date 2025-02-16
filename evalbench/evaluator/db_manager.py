from queue import Queue
from databases import DB, get_database

def build_db_queue(core_db: DB, db_config, query_type: str, num_dbs: int):
    db_queue = Queue[DB]()
    if query_type == "dql":
        # For DQL, use the same single DB with a user that has only DQL access
        singular_db = get_database(db_config)
        for _ in range(num_dbs):
            db_queue.put(singular_db)
    elif query_type == "dml":
        # For DML, use the same single DB with a user that has only DQL / DML access
        singular_db = get_database(db_config)
        for _ in range(num_dbs):
            db_queue.put(singular_db)
    elif query_type == "ddl":
        raise ValueError("nope")
        # For DDL, use a different tmp DB with a user that has all types of access
        # Every DB is setup / torndown constantly without data insertions.
        # for _ in range(num_dbs):
        #    tmp_db = get_database(db_config)
        #    db_queue.put(tmp_db)
    return db_queue