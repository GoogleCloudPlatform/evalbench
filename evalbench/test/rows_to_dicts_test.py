import hashlib
import pickle
import tempfile
from types import SimpleNamespace

import sqlparse
from google.cloud.bigtable.data.execute_query import QueryResultRow

from databases.bigtable import BigtableDB
from databases.sqlite import SQLiteDB
from databases.util import rows_to_dicts, with_cache_execute
from scorers.exactmatcher import ExactMatcher
from scorers.setmatcher import SetMatcher


class _FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


def test_rows_to_dicts_keeps_columns_that_share_a_name():
    rows = rows_to_dicts(["name", "name"], [("Monaco Grand Prix", "Circuit de Monaco")])
    assert rows == [{"name": "Monaco Grand Prix", "name_1": "Circuit de Monaco"}]


def test_rows_to_dicts_suffix_skips_an_existing_column_name():
    rows = rows_to_dicts(["name", "name_1", "name"], [("a", "b", "c")])
    assert rows == [{"name": "a", "name_1": "b", "name_2": "c"}]


def test_rows_to_dicts_without_repeated_names_matches_dict_zip():
    columns = ["id", "name"]
    rows = [(1, "a"), (2, "b")]
    assert rows_to_dicts(columns, rows) == [dict(zip(columns, row)) for row in rows]


def test_bigtable_rows_keep_columns_that_share_a_name():
    row = QueryResultRow()
    row.add_field("name", "Monaco Grand Prix")
    row.add_field("name", "Circuit de Monaco")
    db = BigtableDB.__new__(BigtableDB)
    db.instance = SimpleNamespace(instance_id="test-instance")
    db.data_client = SimpleNamespace(execute_query=lambda query, instance_id: iter([row]))

    result, error = db._execute_query("SELECT r.name, c.name FROM races r JOIN circuits c")

    assert error is None
    assert result == [{"name": "Monaco Grand Prix", "name_1": "Circuit de Monaco"}]


def test_cached_results_written_before_the_row_format_change_are_not_reused():
    query = "SELECT r.name, c.name FROM races r JOIN circuits c ON r.circuit_id = c.id"
    engine_url = "sqlite:///f1"
    cache = _FakeCache()
    # Entry stored by an earlier version: same key format, one merged column.
    formatted = sqlparse.format(query, reindent=True, keyword_case="upper")
    old_key = hashlib.sha256((formatted + engine_url).encode()).hexdigest()
    cache.store[old_key] = pickle.dumps([{"name": "Circuit de Monaco"}])
    fresh = [{"name": "Monaco Grand Prix", "name_1": "Circuit de Monaco"}]

    result, _, error = with_cache_execute(query, engine_url, lambda q: (fresh, None, None), cache)

    assert error is None
    assert result == fresh


def test_set_and_exact_match_see_both_columns_of_a_join():
    golden_query = "SELECT r.name, c.name FROM races r JOIN circuits c ON r.circuit_id = c.id"
    incomplete_query = "SELECT c.name FROM races r JOIN circuits c ON r.circuit_id = c.id"
    with tempfile.TemporaryDirectory() as tmp:
        db = SQLiteDB({
            "database_path": tmp,
            "database_name": "f1",
            "db_type": "sqlite",
            "max_executions_per_minute": 1000,
        })
        try:
            for statement in [
                "CREATE TABLE circuits (id INTEGER, name TEXT);",
                "CREATE TABLE races (id INTEGER, circuit_id INTEGER, name TEXT);",
                "INSERT INTO circuits VALUES (1, 'Circuit de Monaco');",
                "INSERT INTO races VALUES (10, 1, 'Monaco Grand Prix');",
            ]:
                _, _, error = db.execute(statement)
                assert not error
            golden, _, golden_error = db.execute(golden_query)
            generated, _, generated_error = db.execute(incomplete_query)
        finally:
            db.close_connections()

    assert golden == [{"name": "Monaco Grand Prix", "name_1": "Circuit de Monaco"}]
    for matcher in (SetMatcher({}), ExactMatcher({})):
        score, _ = matcher.compare(
            "", golden_query, "dql", golden, None, golden_error,
            incomplete_query, generated, None, generated_error,
        )
        assert score == 0
