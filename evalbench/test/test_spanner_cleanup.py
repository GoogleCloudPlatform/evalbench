import pytest
from unittest.mock import MagicMock, patch
from evalbench.databases.spanner import SpannerDB


@pytest.fixture
def mock_spanner_db():
    config = {
        "database_path": "projects/p/instances/i",
        "database_name": "d",
        "db_type": "spanner",
        "max_executions_per_minute": 60,
        "gcp_project_id": "test_project",
        "instance_id": "test_instance"
    }
    with patch("google.cloud.spanner.Client"):
        db = SpannerDB(config)
        db.database = MagicMock()
        db.expected_dialect_str = "GOOGLE_STANDARD_SQL"
        return db


def test_drop_views(mock_spanner_db):
    mock_snapshot = mock_spanner_db.database.snapshot.return_value.__enter__.return_value
    mock_snapshot.execute_sql.return_value = [["view1"], ["view2"]]

    mock_spanner_db._drop_views()

    mock_snapshot.execute_sql.assert_called_once()
    assert mock_spanner_db.database.update_ddl.called
    args, _ = mock_spanner_db.database.update_ddl.call_args
    assert args[0] == ["DROP VIEW `view1`", "DROP VIEW `view2`"]


def test_drop_indices(mock_spanner_db):
    mock_snapshot = mock_spanner_db.database.snapshot.return_value.__enter__.return_value
    mock_snapshot.execute_sql.return_value = [["idx1"], ["idx2"]]

    mock_spanner_db._drop_indices()

    mock_snapshot.execute_sql.assert_called_once()
    assert mock_spanner_db.database.update_ddl.called
    args, _ = mock_spanner_db.database.update_ddl.call_args
    assert args[0] == ["DROP INDEX `idx1`", "DROP INDEX `idx2`"]


def test_drop_foreign_keys(mock_spanner_db):
    mock_snapshot = mock_spanner_db.database.snapshot.return_value.__enter__.return_value
    mock_snapshot.execute_sql.return_value = [["table1", "fk1"], ["table2", "fk2"]]

    mock_spanner_db._drop_foreign_keys()

    mock_snapshot.execute_sql.assert_called_once()
    assert mock_spanner_db.database.update_ddl.called
    args, _ = mock_spanner_db.database.update_ddl.call_args
    assert args[0] == ["ALTER TABLE `table1` DROP CONSTRAINT `fk1`", "ALTER TABLE `table2` DROP CONSTRAINT `fk2`"]


def test_drop_tables(mock_spanner_db):
    mock_snapshot = mock_spanner_db.database.snapshot.return_value.__enter__.return_value
    mock_snapshot.execute_sql.return_value = [["table1"], ["table2"]]

    mock_spanner_db._drop_tables()

    mock_snapshot.execute_sql.assert_called_once()
    assert mock_spanner_db.database.update_ddl.called
    args, _ = mock_spanner_db.database.update_ddl.call_args
    assert args[0] == ["DROP TABLE `table1`", "DROP TABLE `table2`"]
