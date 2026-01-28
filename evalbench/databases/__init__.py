from .postgres import PGDB
from .mysql import MySQLDB
from .sqlserver import SQLServerDB
from .sqlite import SQLiteDB
from .db import DB
from .bigquery import BQDB
from .bigtable import BigtableDB
from .alloydb import AlloyDB
from .alloydb_omni import AlloyDBOmni
from .spanner import SpannerDB
from .mongodb import MongoDB


def get_database(db_config, db_name) -> DB:
    # if db_name is provided:
    #   - It will override the provided default database_name
    #   - This is useful as the default db may be "postgres" or a default only used for setup
    if db_name:
        db_config["database_name"] = db_name

    if db_config["db_type"] == "postgres":
        return PGDB(db_config)
    if db_config["db_type"] == "spanner":
        return SpannerDB(db_config)
    if db_config["db_type"] == "mysql":
        return MySQLDB(db_config)
    if db_config["db_type"] == "sqlserver":
        return SQLServerDB(db_config)
    if db_config["db_type"] == "sqlite":
        return SQLiteDB(db_config)
    if db_config["db_type"] == "bigquery":
        return BQDB(db_config)
    if db_config["db_type"] == "alloydb":
        return AlloyDB(db_config)
    if db_config["db_type"] == "alloydb_omni":
        return AlloyDBOmni(db_config)
    if db_config["db_type"] == "bigtable":
        return BigtableDB(db_config)
    if db_config["db_type"] == "mongodb":
        return MongoDB(db_config)
    raise ValueError("DB Type not Supported")
