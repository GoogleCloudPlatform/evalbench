from .postgres import PGDB
from .mysql import MySQLDB
from .sqlserver import SQLServerDB
from .sqlite import SQLiteDB
from .db import DB


def get_database(db_config) -> DB:
    if db_config["db_type"] == "postgres":
        return PGDB(db_config)
    if db_config["db_type"] == "mysql":
        return MySQLDB(db_config)
    if db_config["db_type"] == "sqlserver":
        return SQLServerDB(db_config)
    if db_config["db_type"] == "sqlite":
        return SQLiteDB(db_config)
    raise ValueError("DB Type not Supported")
