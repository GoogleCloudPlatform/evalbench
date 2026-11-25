"""Tests for the database identity in the query cache key."""

import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from databases.mysql import MySQLDB
from databases.postgres import PGDB
from databases.sqlite import SQLiteDB
from databases.sqlserver import SQLServerDB

_QUERY = "SELECT id FROM t"


class _FakeCache:

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


def _connector_db(db_class, cache, url, rows, **identity):
    """Returns a database object that does not connect to a server."""
    db = db_class.__new__(db_class)
    db.db_type = identity.get("db_type", "postgres")
    db.db_path = identity.get("db_path", "project:region:instance")
    db.db_name = identity.get("db_name", "db")
    db.username = identity.get("username", "user")
    db.cache_client = cache
    db.engine = SimpleNamespace(url=url)
    db._execute = lambda query, eval_query=None, rollback=False: (
        rows, None, None)
    return db


class QueryCacheKeyTest(unittest.TestCase):

    def test_connector_databases_do_not_share_cached_rows(self):
        cases = [
            (PGDB, "postgresql+pg8000://"),
            (MySQLDB, "mysql+pymysql://"),
            (SQLServerDB, "mssql+pytds://"),
        ]
        differences = [
            {"db_name": "other_db"},
            {"db_path": "project:region:other-instance"},
            {"username": "other_user"},
        ]
        for db_class, url in cases:
            for difference in differences:
                with self.subTest(db_class=db_class.__name__, **difference):
                    cache = _FakeCache()
                    first = _connector_db(db_class, cache, url, [{"id": 1}])
                    second = _connector_db(
                        db_class, cache, url, [{"id": 2}], **difference)

                    self.assertEqual(
                        first.execute(_QUERY, use_cache=True)[0], [{"id": 1}])
                    self.assertEqual(
                        second.execute(_QUERY, use_cache=True)[0], [{"id": 2}])

    def test_same_database_uses_the_cache(self):
        cache = _FakeCache()
        first = _connector_db(PGDB, cache, "postgresql+pg8000://", [{"id": 1}])
        second = _connector_db(PGDB, cache, "postgresql+pg8000://", [{"id": 2}])

        first.execute(_QUERY, use_cache=True)

        self.assertEqual(second.execute(_QUERY, use_cache=True)[0], [{"id": 1}])

    def test_sqlite_databases_with_the_same_name_do_not_share_rows(self):
        cache = _FakeCache()
        with tempfile.TemporaryDirectory() as tmp:
            results = []
            for directory, value in (("a", 1), ("b", 2)):
                db_path = os.path.join(tmp, directory)
                os.makedirs(db_path)
                conn = sqlite3.connect(os.path.join(db_path, "shop.db"))
                conn.execute("CREATE TABLE t (id INT)")
                conn.execute("INSERT INTO t VALUES (?)", (value,))
                conn.commit()
                conn.close()
                db = SQLiteDB({
                    "db_type": "sqlite",
                    "database_path": db_path,
                    "database_name": "shop",
                    "max_executions_per_minute": 1000,
                })
                db.cache_client = cache
                results.append(db.execute(_QUERY, use_cache=True)[0])

        self.assertEqual(results, [[{"id": 1}], [{"id": 2}]])


if __name__ == "__main__":
    unittest.main()
