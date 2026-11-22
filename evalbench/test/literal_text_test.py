"""Queries with a colon inside a string literal run as written."""

import tempfile
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mssql, mysql, postgresql, sqlite

from databases.mysql import MySQLDB
from databases.postgres import PGDB
from databases.sqlite import SQLiteDB
from databases.sqlserver import SQLServerDB
from databases.util import literal_text

# The LIKE pattern of the gold SQL of BIRD dev questions 938, 959, 989 and 990.
TIME_QUERY = "SELECT time FROM results WHERE time LIKE '_:%:__.___'"
COUNT_QUERY = "SELECT COUNT(*) AS n FROM results WHERE time LIKE '%:__.___'"
INSERT_COMMAND = "INSERT INTO results VALUES ('1:34:50.616', '{\"laps\":58}');"

DIALECTS = {
    "sqlite": sqlite.dialect(),
    "mysql": mysql.dialect(),
    "postgresql": postgresql.dialect(),
    "mssql": mssql.dialect(),
}


class _RecordingConnection:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def begin(self):
        return self

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(returns_rows=False)


def _use_recording_connection(db):
    connection = _RecordingConnection()
    db.engine = SimpleNamespace(connect=lambda: connection)
    db.execs_per_minute = None
    db.semaphore = None
    db.max_attempts = 1
    return connection


def _assert_runs_as_written(statement, dialect, query):
    compiled = statement.compile(dialect=dialect)
    assert compiled.params == {}
    # Some dialects double "%" for the driver. Nothing else may change.
    assert str(compiled).replace("%%", "%") == query


@pytest.mark.parametrize("dialect_name", list(DIALECTS))
@pytest.mark.parametrize(
    "query",
    [
        TIME_QUERY,
        "SELECT '{\"laps\":58}' AS info",
        "SELECT ' :x'::text AS t",
        "SELECT 'C:\\:x' AS path",
        "SELECT 1",
    ],
)
def test_literal_text_has_no_bind_parameters(dialect_name, query):
    _assert_runs_as_written(literal_text(query), DIALECTS[dialect_name], query)


def test_sqlite_runs_queries_with_a_colon_in_a_string_literal():
    with tempfile.TemporaryDirectory() as tmp:
        db = SQLiteDB({
            "database_path": tmp,
            "database_name": "f1",
            "db_type": "sqlite",
            "max_executions_per_minute": 1000,
        })
        try:
            db.batch_execute([
                "CREATE TABLE results (time TEXT, info TEXT);",
                INSERT_COMMAND,
                "INSERT INTO results VALUES ('+5.478', '{\"laps\":58}');",
            ])
            result, eval_result, error = db.execute(TIME_QUERY, eval_query=COUNT_QUERY)
        finally:
            db.close_connections()

    assert error is None
    assert result == [{"time": "1:34:50.616"}]
    assert eval_result == [{"n": 1}]


@pytest.mark.parametrize(
    "db_class, dialect_name", [(MySQLDB, "mysql"), (SQLServerDB, "mssql")]
)
def test_execute_queries_sends_the_query_as_written(db_class, dialect_name):
    db = db_class.__new__(db_class)
    connection = _RecordingConnection()

    db._execute_queries(connection, TIME_QUERY)

    [statement] = connection.statements
    _assert_runs_as_written(statement, DIALECTS[dialect_name], TIME_QUERY)


def test_mysql_sends_the_batch_commands_as_written():
    db = MySQLDB.__new__(MySQLDB)
    connection = _use_recording_connection(db)

    db.batch_execute([INSERT_COMMAND])

    _, insert_statement = connection.statements
    _assert_runs_as_written(insert_statement, DIALECTS["mysql"], INSERT_COMMAND)


def test_postgres_sends_the_query_and_the_eval_query_as_written():
    db = PGDB.__new__(PGDB)
    connection = _use_recording_connection(db)

    _, _, error = db._execute(TIME_QUERY, eval_query=COUNT_QUERY)

    assert error is None
    query_statement, eval_statement = connection.statements
    _assert_runs_as_written(query_statement, DIALECTS["postgresql"], TIME_QUERY)
    _assert_runs_as_written(eval_statement, DIALECTS["postgresql"], COUNT_QUERY)
