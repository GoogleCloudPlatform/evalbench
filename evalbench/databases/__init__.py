from .postgres import PGDB
from .alloydb import AlloyDB
from .mysql import MySQLDB
from .sqlserver import SQLServerDB
from .sqlite import SQLiteDB
from .db import DB


def get_database(db_config) -> DB:
    if db_config["db"] == "alloydb":
        return AlloyDB(db_config)
    if db_config["db"] == "postgres":
        return PGDB(db_config)
    if db_config["db"] == "mysql":
        return MySQLDB(db_config)
    if db_config["db"] == "sqlserver":
        return SQLServerDB(db_config)
    if db_config["db"] == "sqlite":
        return SQLiteDB(db_config)
    raise ValueError("DB Type not Supported")
