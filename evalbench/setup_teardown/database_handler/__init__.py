from .mysql_handler import MYSQLHandler
from .postgres_handler import PostgresHandler
from databases import DB, PGDB, MySQLDB
from .db_handler import DBHandler


def get_db_handler(db: DB, database_name: str) -> DBHandler:
    if isinstance(db, MySQLDB):
        return MYSQLHandler(db, database_name)
    elif isinstance(db, PGDB):
        return PostgresHandler(db, database_name)
    else:
        raise ValueError(f"Unsupported database type provided for setup / teardown.")
