import pytest
from evalbench.databases.mysql import MySQLDB
from evalbench.databases.postgres import PGDB
from evalbench.databases.sqlite import SQLiteDB


def test_mysql_clean_insert_value():
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), None) is None
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "null") is None
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "None") is None
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "") is None
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "'some_value'") == "some_value"
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "true") == 1
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "True") == 1
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "false") == 0
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "False") == 0
    assert MySQLDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": MySQLDB._format_boolean_value})(), "123") == "123"


def test_pg_clean_insert_value():
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), None) is None
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "null") is None
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "None") is None
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "") is None
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "'some_value'") == "some_value"
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "true") == "true"
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "True") == "true"
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "false") == "false"
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "False") == "false"
    assert PGDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": PGDB._format_boolean_value})(), "123") == "123"


def test_sqlite_clean_insert_value():
    assert SQLiteDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": SQLiteDB._format_boolean_value})(), None) is None
    assert SQLiteDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": SQLiteDB._format_boolean_value})(), "null") is None
    assert SQLiteDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": SQLiteDB._format_boolean_value})(), "None") is None
    assert SQLiteDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": SQLiteDB._format_boolean_value})(), "") is None
    assert SQLiteDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": SQLiteDB._format_boolean_value})(), "'some_value'") == "some_value"
    assert SQLiteDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": SQLiteDB._format_boolean_value})(), "true") == "true"
    assert SQLiteDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": SQLiteDB._format_boolean_value})(), "False") == "False"
    assert SQLiteDB._clean_insert_value(type("obj", (object,), {"_format_boolean_value": SQLiteDB._format_boolean_value})(), "123") == "123"
